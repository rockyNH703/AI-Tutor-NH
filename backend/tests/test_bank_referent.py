"""What "this" means in a quiz request (app/chat/bank_turn.py referent()): pure, no database.

Session 1's headed Quiz me check (2026-11-20): after "Quiz me on ospf", "quiz me on the BGP in the Real World course"
and "next 5", the button's "Quiz me on this" served Wireless questions, because the earlier messages were read word
by word as loose sentences ("real world course" drifted to Wireless)."""

from app.chat.bank_turn import referent


def test_this_after_a_bank_test_means_that_test_not_the_words_of_the_requests():
    chat = ["next 5", "quiz me on the BGP in the Real World course", "Quiz me on ospf"]  # newest first
    assert referent(chat) == [("last", "")]


def test_this_after_a_named_quiz_request_means_its_topic():
    assert referent(["quiz me on vlans", "hello"]) == [("topic", "vlans")]


def test_this_after_ordinary_messages_reads_them_as_sentences_newest_first():
    chat = ["why does ospf get stuck in exstart?", "my R1 config is attached", "thanks"]
    assert referent(chat) == [("sentence", chat[0]), ("sentence", chat[1]), ("sentence", chat[2])]


def test_a_sentence_before_a_quiz_request_is_read_first_then_the_request_settles_it():
    chat = ["can you explain the dr election?", "quiz me on ospf areas"]
    assert referent(chat) == [("sentence", chat[0]), ("topic", "ospf areas")]
