"""Mémoire de Jarvis : retenir, oublier, réciter, et ne donner au modèle que ce qui concerne la question."""
import json

import pytest

import jarvis.tools.builtin as builtin
from jarvis import commands, prompts
from jarvis.desktop import soft as desktop_soft
from jarvis.memory import MAX_FACTS, Memory, looks_secret, soft, third_person


@pytest.fixture
def memory(tmp_path):
    return Memory(tmp_path / "memory.json")


def test_remembering_survives_a_restart(memory, tmp_path):
    assert memory.remember("je travaille sur le projet jarvis-vocal") == "C'est noté, je m'en souviendrai."
    assert memory.remember("que ma sœur s'appelle Léa") == "C'est noté, je m'en souviendrai."
    again = Memory(tmp_path / "memory.json")                        # nouveau démarrage de Jarvis
    assert again.facts() == ["Je travaille sur le projet jarvis-vocal.", "Ma sœur s'appelle Léa."]
    assert memory.remember("Je travaille sur le projet jarvis-vocal.") == "Je le savais déjà."


def test_forgetting_finds_the_closest_fact(memory):
    memory.remember("je préfère le café sans sucre")
    memory.remember("je me lève à 7 h en semaine")
    assert memory.forget("le café sans sucre").startswith("C'est oublié : Je préfère le café sans sucre")
    assert memory.facts() == ["Je me lève à 7 h en semaine."]
    assert memory.forget("mon groupe préféré") == "Je n'avais rien retenu de tel."
    assert memory.forget_all() == "J'ai tout oublié, 1 souvenir."
    assert memory.facts() == []
    assert memory.recite().startswith("Je n'ai encore rien retenu")


def test_secrets_are_never_remembered(memory):
    for secret in ("mon mot de passe est chaton42", "le code pin de ma carte est 1234",
                   "ma carte bancaire c'est 4970 1234 5678 9012",
                   "ma clé API sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"):
        assert "ne retiens jamais" in memory.remember(secret), secret
    assert memory.facts() == []
    assert not looks_secret("je me lève à 7 h")
    assert not looks_secret("mon PC s'appelle Kiwi")


def test_memory_is_bounded(memory):
    for index in range(MAX_FACTS):
        memory.remember(f"le fait numéro {index} est vrai")
    assert "J'ai oublié le plus ancien" in memory.remember("un fait de trop")
    assert len(memory.facts()) == MAX_FACTS
    assert "fait numéro 0 " not in " ".join(memory.facts())
    assert "trop long" in memory.remember("x " * 150)


def test_a_damaged_file_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("{ ceci n'est pas du json", encoding="utf-8")
    memory = Memory(path)
    assert memory.facts() == []
    assert list(tmp_path.glob("memory.abime-*.json"))                # l'ancien contenu est gardé de côté
    memory.remember("je repars de zéro")
    assert json.loads(path.read_text(encoding="utf-8"))["facts"][0]["text"] == "Je repars de zéro."


def test_facts_are_rewritten_so_the_model_knows_who_is_who():
    """Mesuré : lire « je travaille sur jarvis » faisait dire au modèle « Je travaille sur Jarvis »."""
    assert third_person("Je travaille sur le projet jarvis-vocal.", "Sacha") == \
        "Sacha travaille sur le projet jarvis-vocal."
    assert third_person("Je me lève à 7 h en semaine.", "Sacha") == "Sacha se lève à 7 h en semaine."
    assert third_person("J'ai un chat qui s'appelle Moka.", "Sacha") == "Sacha a un chat qui s'appelle Moka."
    assert third_person("Je suis allergique aux noix.", "Sacha") == "Sacha est allergique aux noix."
    assert third_person("Ma sœur s'appelle Léa.", "Sacha") == "À propos de Sacha : sa sœur s'appelle Léa."


def test_only_relevant_memories_reach_the_model(memory):
    """Mesuré : toute la mémoire dans le prompt, et le modèle la récite partout (« Rome. Je préfère le café »)."""
    for fact in ("je travaille sur le projet jarvis-vocal", "ma sœur s'appelle Léa", "je préfère le café sans sucre",
                 "je me lève à 7 h en semaine", "mon PC fixe s'appelle Kiwi"):
        memory.remember(fact)
    assert "Léa" in memory.relevant("Comment s'appelle ma sœur ?", "Sacha")
    assert "Léa" in memory.relevant("comment s'appelle ma soeur", "Sacha")      # sans la ligature
    assert "Kiwi" in memory.relevant("Comment s'appelle mon PC fixe ?", "Sacha")
    assert "Léa" not in memory.relevant("Comment s'appelle mon PC fixe ?", "Sacha")   # « s'appelle » ne suffit pas
    assert "sans sucre" in memory.relevant("Tu te souviens comment j'aime mon café ?", "Sacha")
    assert memory.relevant("Quelle est la capitale de l'Italie ?", "Sacha") == ""
    assert memory.relevant("Donne-moi une idée de dessert", "Sacha") == ""


def test_the_system_prompt_stays_stable_when_memory_changes(memory):
    """Les souvenirs passent juste avant la question : le prompt système, lui, garde son cache."""
    before = prompts.system_prompt("Sacha", __import__("datetime").date(2026, 9, 16), tools=True)
    memory.remember("je travaille sur jarvis")
    after = prompts.system_prompt("Sacha", __import__("datetime").date(2026, 9, 16), tools=True)
    assert before == after


@pytest.mark.parametrize(("phrase", "arguments"), [
    ("retiens que je travaille sur le projet jarvis-vocal", {"action": "remember",
                                                             "text": "je travaille sur le projet jarvis-vocal"}),
    ("Jarvis, souviens-toi que je préfère le café sans sucre.", {"action": "remember",
                                                                  "text": "je préfère le café sans sucre"}),
    ("note que ma sœur s'appelle Léa", {"action": "remember", "text": "ma sœur s'appelle Léa"}),
    ("Tu peux retenir que mon PC s'appelle Kiwi ?", {"action": "remember", "text": "mon PC s'appelle Kiwi"}),
    ("oublie que je préfère le café", {"action": "forget", "text": "je préfère le café"}),
    ("oublie tout", {"action": "forget_all"}),
    ("qu'est-ce que tu sais sur moi", {"action": "list"}),
])
def test_memory_phrases_need_no_llm(phrase, arguments):
    command = commands.parse(phrase)
    assert command.tool == "memory" and command.arguments == arguments


def test_memory_phrases_do_not_steal_other_commands():
    assert commands.parse("note acheter du pain") is None
    assert commands.parse("enregistre le fichier") is None
    assert commands.parse("cherche le fichier rapport").tool == "files"


def test_the_memory_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(builtin, "MEMORY", Memory(tmp_path / "memory.json"))
    assert builtin.memory_tool("remember", "je suis végétarien") == "C'est noté, je m'en souviendrai."
    assert "Je suis végétarien." in builtin.memory_tool("list")
    assert builtin.memory_tool("forget", "végétarien").startswith("C'est oublié")
    assert builtin.memory_tool("forget_all") == "Je n'avais rien retenu."


def test_ligatures_no_longer_break_words():
    """« sœur » devenait « s ur » : le mot disparaissait de toute recherche."""
    assert soft("Sœur, cœur") == "soeur coeur"
    assert desktop_soft("Sœur, cœur") == "soeur coeur"
