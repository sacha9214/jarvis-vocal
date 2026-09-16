from jarvis.text.chunker import SpeechChunker, clean_for_speech


def feed_all(tokens: list[str]) -> list[str]:
    chunker = SpeechChunker()
    pieces = []
    for token in tokens:
        pieces += chunker.feed(token)
    return pieces + chunker.flush()


def test_first_chunk_leaves_at_a_comma_once_long_enough():
    chunker = SpeechChunker()
    assert chunker.feed("D'accord, ") == []          # trop court : on attend la suite
    assert chunker.feed("j'allume la lumière du salon, ") == ["D'accord, j'allume la lumière du salon,"]


def test_later_chunks_wait_for_the_end_of_the_sentence():
    chunker = SpeechChunker()
    assert chunker.feed("Oui, bien sûr. ") == ["Oui, bien sûr."]
    assert chunker.feed("Il fait beau, ") == []      # plus de coupure à la virgule
    assert chunker.feed("et chaud. Voilà") == ["Il fait beau, et chaud."]
    assert chunker.flush() == ["Voilà"]


def test_a_tiny_piece_is_spoken_with_the_next_one():
    # Mesuré : Pocket TTS rate ~40 % des énoncés d'un ou deux mots, 2 % à partir de trois.
    chunker = SpeechChunker()
    assert chunker.feed("Oui. ") == []                                   # « Oui. » seul bafouillerait
    assert chunker.feed("D'accord. ") == []                              # « Oui. D'accord. » aussi
    assert chunker.feed("Je ferme Discord. ") == ["Oui. D'accord. Je ferme Discord."]
    assert chunker.feed("Voilà. ") == []
    assert chunker.flush() == ["Voilà."]                                 # dernier morceau : dit quand même
    assert feed_all(["Pause. ", "C'est fait."]) == ["Pause. C'est fait."]
    assert feed_all(["Il est 21 heures 7."]) == ["Il est 21 heures 7."]


def test_decimals_and_abbreviations_do_not_split():
    assert feed_all(["Il fait 3.5 degrés chez M. Dupont. ", "Fin."]) == ["Il fait 3.5 degrés chez M. Dupont.", "Fin."]


def test_markdown_and_emojis_are_not_spoken():
    assert clean_for_speech("**Super** idée 🎉 !") == "Super idée !"
    assert clean_for_speech("- premier point") == "premier point"


def test_text_without_punctuation_is_still_cut():
    pieces = feed_all(["mot " * 80])
    assert len(pieces) > 1
    assert all(len(piece) <= 200 for piece in pieces)
