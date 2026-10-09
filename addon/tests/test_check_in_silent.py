"""Scheduled check-ins must be able to actually stay silent.

Prior to this fix a check-in whose instruction said "if no update, stay
silent" would still send ZOE's paren-wrapped planning note to WhatsApp
("(אין מה לשלוח — הסריקה לא מצאה...)"). Covers both the explicit SILENT
sentinel from the new framing and the historical paren-wrapped failure
mode so a regression gets caught by the suite, not by the household.
"""
from app.agent_loop import _is_silent_sentinel


class TestExplicitSentinel:
    def test_bare_sentinel(self):
        assert _is_silent_sentinel("SILENT")

    def test_sentinel_with_period(self):
        assert _is_silent_sentinel("SILENT.")

    def test_sentinel_with_whitespace(self):
        assert _is_silent_sentinel("  SILENT  \n")

    def test_sentinel_lowercase(self):
        assert _is_silent_sentinel("silent")

    def test_sentinel_wrapped_in_parens(self):
        # Model wrote (SILENT) instead of bare SILENT — still means stay silent.
        assert _is_silent_sentinel("(SILENT)")

    def test_two_word_variant(self):
        assert _is_silent_sentinel("STAY SILENT")

    def test_normal_message_is_not_sentinel(self):
        assert not _is_silent_sentinel("בוקר טוב! יש לך פגישה ב-10:00.")

    def test_short_word_containing_silent_is_not_sentinel(self):
        # "silently" alone IS treated as sentinel (model variant); a sentence
        # that merely contains the word is not.
        assert not _is_silent_sentinel("The dishwasher ran silently last night.")


class TestParenWrappedMetaCommentary:
    """The failure mode we actually saw in production — model narrates its
    stay-silent decision instead of actually staying silent."""

    def test_hebrew_nothing_to_send(self):
        text = (
            "(אין מה לשלוח — לא הגיע מייל חדש מעיינת/גובינס; האחרון ממנה הוא "
            'עדיין מ-13:40 אתמול בנושא "רכב פניה 2017148145" שכבר דווח לניר. '
            "לפי הנחייתי, סריקה ללא תוצאה נשארת שקטה.)"
        )
        assert _is_silent_sentinel(text)

    def test_english_nothing_to_send(self):
        assert _is_silent_sentinel("(Nothing to send — no update since the last check.)")

    def test_english_staying_silent(self):
        assert _is_silent_sentinel("(Scanned the inbox — no match, staying silent.)")

    def test_hebrew_no_update(self):
        assert _is_silent_sentinel("(אין עדכון חדש בנושא הזה, נשארת שקטה.)")

    def test_hebrew_scan_without_result(self):
        assert _is_silent_sentinel("(סריקה ללא תוצאה — אין מה לדווח כרגע.)")

    def test_actual_message_wrapped_in_parens_is_not_silent(self):
        """A paren-wrapped sentence that ISN'T a stay-silent narration must
        still go out — e.g. a legit side-comment in parens."""
        text = "(אגב, יש לך פגישה חדשה שהתווספה ליומן.)"
        assert not _is_silent_sentinel(text)

    def test_message_mentioning_silence_in_body_not_silent(self):
        """Just mentioning 'nothing' in a real update must not trigger the
        paren guard — the guard requires full paren wrapping."""
        text = "המקרר שקט שוב, שום דבר לא דלוק."
        assert not _is_silent_sentinel(text)


class TestEdgeCases:
    def test_empty_string(self):
        assert not _is_silent_sentinel("")

    def test_whitespace_only(self):
        assert not _is_silent_sentinel("   \n  ")

    def test_single_paren_not_sentinel(self):
        assert not _is_silent_sentinel("(")

    def test_unmatched_parens_not_sentinel(self):
        assert not _is_silent_sentinel("(nothing to send")
