"""K-04a: the practice-test repair (QUESTION_BANK_TESTS) never rewrites engineering content.

A shell test such as `[[ -f "$file" ]]` is bash syntax, not a blank to fill in: bash needs the spaces inside its
brackets, and an exercise's blank never has them (`[[OSPF]]`, `[[0|zero]]`).
"""

from app.qbank.chat_tests import _fenced

STEPS = """:::steps[Check the SSH config before a restart]
Run `sudo sshd -t` first, then:

[[ -f /etc/ssh/sshd_config ]] && systemctl reload ssh
:::"""


def test_a_card_with_a_bash_test_is_returned_byte_for_byte():
    reply = f"Before you reload SSH:\n\n{STEPS}\n\nThat keeps your session open."
    assert _fenced(reply) == reply


def test_json_whose_text_is_a_shell_test_is_not_an_exercise():
    reply = 'The script checks a variable: {"text": "[[ -n \\"$VPN_PEER\\" ]] || exit 1"} and stops.'
    assert _fenced(reply) == reply


def test_a_card_with_a_real_blank_still_becomes_an_exercise():
    reply = ":::card[Routing]\nThe link-state protocol that elects a DR is [[OSPF]].\n:::"
    repaired = _fenced(reply)
    assert "```fill-blank" in repaired and "[[OSPF]]" in repaired
