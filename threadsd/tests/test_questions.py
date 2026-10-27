"""Question parsing (Question.options / multi / allow_other / parse in /state).
Run: python3 threadsd/tests/test_questions.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from plugins import parser_numbered  # noqa: E402,F401  (registers the parser)
from plugins._common import message_facts, parse_questions  # noqa: E402

# Two messages in the shape of a real thread's (content made up). The second is the screenshot
# bug: one question with 7 options came out as 7 question cards, with the Maple question mixed in.
RECIPES_FIRST = 'STATUS: needs you\n\n**Your dashboard link**\nhttps://claude.ai/artifact/EXAMPLE — "Recipe Box". It\'s private to you. It has three tabs:\n- **Overview:** recipe and source counts, an import timeline and open questions.\n- **Recipes:** every recipe by course with a one-line summary, plus a filter box.\n- **How it works:** where the files live and how to add a source from your phone.\n\nThe dashboard\'s own load check found no errors, but I haven\'t looked at it rendered, so tell me if anything looks off.\n\n**What I imported**\n- **Your bookmarks folder:** 41 recipes, cleaned up into markdown.\n  - I left out the restaurant reservations on purpose: they\'re not recipes.\n\n**Page counts**\n58 pages plus the index:\n- 41 recipes\n- 12 techniques\n- 5 ingredient guides\n\n**Which recipe sources should I add?**\nI haven\'t touched any of these. Reply with numbers, e.g. "1, 3":\n1. **RSS feeds:** paste the feed links and I\'ll check them daily.\n2. **Email newsletters:** needs a filter in your mail app; you\'d set it up yourself.\n3. **Google Drive:** through your signed-in Chrome. No new permissions.\n4. **Paper notebook:** photograph the pages and tell me the folder.\n5. **Photos:** dish photos go into `raw/assets/`. Needs Photos access.\n6. **Browser bookmarks:** Chrome needs nothing extra; Safari needs Full Disk Access.\n7. **None for now.**\n\n**Two quick checks for your pages**\n1. Your notes sometimes call you "Chef". Which name should the site use for you?\n2. Is "Maple" (the person behind the baking rules) a teammate or you?'

RECIPES_SECOND = 'STATUS: needs you\n\n**Name to use: Chef**\nThe site now calls you Chef everywhere.\n- Your page is now `site/people/chef.md`, titled "Chef (me)".\n- Every link to your page, the index and the rules file (`CLAUDE.md`) now say Chef.\n- I committed it locally and refreshed the dashboard. Open questions dropped from 11 to 10.\n\n**Still waiting on you**\n1. **Which recipe sources should I add?** Reply with numbers, e.g. "1, 3":\n   1. RSS feeds: paste the feed links; I check them daily.\n   2. Email newsletters: you\'d set up the mail filter yourself.\n   3. Google Drive: through your signed-in Chrome; nothing new to allow.\n   4. Paper notebook: tell me where the photos are.\n   5. Photos: needs Photos access.\n   6. Browser bookmarks: Chrome needs nothing; Safari needs Full Disk Access.\n   7. None for now.\n2. **Is "Maple" a teammate or you?** Maple is the name behind the baking rules in your notes.\n\nDashboard: https://claude.ai/artifact/EXAMPLE'


def labels(q):
    return [o["label"] for o in q["options"]]


class QuestionsTest(unittest.TestCase):
    def test_recipes_nested_options_are_one_question(self):
        qs = parse_questions("s", RECIPES_SECOND)
        self.assertEqual(len(qs), 2)
        q, maple = qs
        self.assertTrue(q["text"].startswith("Which recipe sources should I add?"))
        self.assertEqual(labels(q), ["RSS feeds", "Email newsletters", "Google Drive", "Paper notebook", "Photos",
                                     "Browser bookmarks", "None for now"])
        self.assertEqual([o["n"] for o in q["options"]], list(range(1, 8)))
        self.assertTrue(q["options"][0]["detail"].startswith("paste the feed links"))
        self.assertTrue(q["multi"])  # "Reply with numbers, e.g. "1, 3""
        self.assertTrue(q["allow_other"])
        self.assertEqual(q["parse"], "heuristic")
        self.assertTrue(maple["text"].startswith('Is "Maple" a teammate or you?'))
        self.assertEqual(maple["options"], [])
        self.assertEqual([x["n"] for x in qs], [1, 2])

    def test_recipes_header_question_then_question_list(self):
        qs = parse_questions("s", RECIPES_FIRST)
        self.assertEqual(len(qs), 3)
        self.assertTrue(qs[0]["text"].startswith("Which recipe sources should I add?"))
        self.assertEqual(len(qs[0]["options"]), 7)
        self.assertEqual(qs[0]["options"][0]["label"], "RSS feeds")
        self.assertIn("Which name should the site use", qs[1]["text"])
        self.assertIn("Maple", qs[2]["text"])
        self.assertEqual(qs[1]["options"] + qs[2]["options"], [])

    def test_marked_single_and_multi(self):
        qs = parse_questions("s", "STATUS: needs you\n\n**Which fix should I ship?** (pick one)\n"
                                  "1. **Retry:** resend after 5 s\n2. **Queue:** hold until idle\n")
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["text"], "Which fix should I ship?")
        self.assertEqual(labels(qs[0]), ["Retry", "Queue"])
        self.assertEqual((qs[0]["multi"], qs[0]["allow_other"], qs[0]["parse"]), (False, True, "marked"))
        qs = parse_questions("s", "STATUS: needs you\n\nWhich tabs should I close? (pick any only)\n"
                                  "1. scratch\n2. codex scratch\n3. perm scratch\n")
        self.assertEqual((qs[0]["multi"], qs[0]["allow_other"], qs[0]["parse"]), (True, False, "marked"))
        self.assertEqual(labels(qs[0]), ["scratch", "codex scratch", "perm scratch"])

    def test_marked_options_nested_in_a_question_list(self):
        qs = parse_questions("s", "STATUS: needs you\n\n1. **Ship now?** (pick one)\n   1. Yes\n   2. No\n"
                                  "2. **Anything else to add?**\n")
        self.assertEqual(len(qs), 2)
        self.assertEqual(labels(qs[0]), ["Yes", "No"])
        self.assertEqual(qs[0]["parse"], "marked")
        self.assertEqual(qs[1]["options"], [])

    def test_colon_list_at_the_end_is_options(self):
        qs = parse_questions("s", "STATUS: needs you\n\nPick a port:\n1. 7878\n2. 8080\n")
        self.assertEqual(len(qs), 1)
        self.assertEqual(labels(qs[0]), ["7878", "8080"])
        self.assertFalse(qs[0]["multi"])

    def test_steps_are_one_question_not_options(self):
        # voice-telemetry's real message: a list of steps under "Then make one call:"
        qs = parse_questions("s", "STATUS: needs you\n\n**What do I need from you?**\nInstall the latest build.\n\n"
                                  "Then make one call:\n1. Let the greeting play to the end.\n2. Say one long sentence.\n"
                                  "3. Hang up with the End button.\n\nAfter that I'll read the trace.")
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["options"], [])
        self.assertTrue(qs[0]["text"].startswith("Then make one call: 1. Let the greeting play"))

    def test_short_question_line_gets_its_heading(self):
        # cloudflare-email's real shape: the choice line says only "Reply with a number:"
        qs = parse_questions("s", "STATUS: needs you\n\n**The cleanup, which needs your OK.** DNS changes:\n"
                                  "1. Delete the old SPF record.\n2. Delete the unused key.\n\nReply with a number:\n1. Do the cleanup: I'll make the changes.\n"
                                  "2. You'll do it yourself (reply 2)\n3. Leave the records as they are for now.\n")
        self.assertEqual(len(qs), 1)  # the change list above isn't a question ("which" there isn't asking)
        self.assertEqual(qs[0]["text"], "The cleanup, which needs your OK. Reply with a number:")
        self.assertEqual(labels(qs[0]), ["Do the cleanup", "You'll do it yourself", "Leave the records as they are for now"])

    def test_option_that_is_a_question_stays_separate(self):
        qs = parse_questions("s", "STATUS: needs you\n\nWhich should I do next?\n1. Fix the parser\n"
                                  "2. Is the old build still in use?\n3. Update the docs\n")
        self.assertEqual(len(qs), 2)
        self.assertEqual(labels(qs[0]), ["Fix the parser", "Update the docs"])
        self.assertEqual(qs[1]["text"], "Is the old build still in use?")

    def test_question_list_without_options(self):
        qs = parse_questions("s", "STATUS: needs you\n\n1. Should I merge?\n2. Which branch is live?\n")
        self.assertEqual([q["text"] for q in qs], ["Should I merge?", "Which branch is live?"])
        self.assertTrue(all(q["options"] == [] and q["parse"] is None for q in qs))

    def test_plain_paragraph_question(self):
        qs = parse_questions("s", "STATUS: needs you\n\nDone with the build. Want me to push it now?")
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["options"], [])

    def test_report_steps_are_not_options(self):
        qs = parse_questions("s", "STATUS: needs you\n\nWhat I did:\n1. Built it\n2. Tested it\n\n"
                                  "1. Should I push?\n")
        self.assertEqual([q["text"] for q in qs], ["Should I push?"])

    def test_bookkeeping_line_does_not_ask(self):
        msgs = [{"role": "assistant", "text": "STATUS: needs you — report saved", "ts": "t", "id": "a"}]
        self.assertIsNone(message_facts(msgs)[1])
        msgs = [{"role": "assistant", "text": RECIPES_SECOND, "ts": "t", "id": "b"}]
        self.assertEqual(message_facts(msgs)[1]["id"], "b")

    def test_ids_are_stable(self):
        self.assertEqual([q["id"] for q in parse_questions("x", RECIPES_SECOND)],
                         [q["id"] for q in parse_questions("x", RECIPES_SECOND)])


if __name__ == "__main__":
    unittest.main()
