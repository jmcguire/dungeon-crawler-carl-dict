"""Regression cases for selecting useful AI descriptions without wiki chrome."""

import unittest

from fandom_dict.extraction import (
    ai_description_paragraph_from_html,
    later_summary_blocks_from_html,
    summary_blocks_from_html,
    summary_from_html,
)


class AiDescriptionTests(unittest.TestCase):
    def test_short_complete_intro_does_not_absorb_ai_text(self) -> None:
        intro = "You're Not Done Yet is a brutal necromancy spell used by War Mages."
        for ai_text in (
            "As part of the description for Levi the 7th:",
            "The description is presented in a television commercial voice:",
            "New Achievement. Bomb's Away!",
            "-",
            "An ordinary unquoted sentence in the AI section.",
        ):
            with self.subTest(ai_text=ai_text):
                html = f"""
                    <p>{intro}</p>
                    <h2><span id="AI_Description">AI Description</span></h2>
                    <p>{ai_text}</p>
                    <blockquote><p>The actual AI explanation.</p></blockquote>
                """
                self.assertEqual(summary_from_html("You're Not Done Yet Spell", html), intro)
                self.assertEqual(later_summary_blocks_from_html(html), [])

    def test_ai_section_tracks_nested_headings_and_stops_at_peer_heading(self) -> None:
        for level in (2, 3, 4):
            with self.subTest(level=level):
                html = f"""
                    <p>A useful intro.</p>
                    <h{level}><span id='AI_Description'>AI Description</span></h{level}>
                    <h{level + 1}><span id="Description">Description</span></h{level + 1}>
                    <blockquote><p>The AI describes this creature.</p></blockquote>
                    <p>A comment about the quoted passage.</p>
                    <h{level}><span id="Story">Story</span></h{level}>
                    <p>The regular story remains available.</p>
                """
                self.assertEqual(summary_blocks_from_html(html), ["A useful intro."])
                self.assertEqual(later_summary_blocks_from_html(html), ["The regular story remains available."])
                self.assertEqual(ai_description_paragraph_from_html(html), "The AI describes this creature.")

    def test_direct_heading_id_and_edit_link_are_supported(self) -> None:
        html = """
            <h3 id='AI_Description'>AI Description <span class='mw-editsection'>edit</span></h3>
            <blockquote><p>A <i>useful</i> explanation.</p></blockquote>
        """
        self.assertEqual(ai_description_paragraph_from_html(html), "A <i>useful</i> explanation.")

    def test_ai_fallback_does_not_cross_into_another_section(self) -> None:
        for heading in ('<h2>Story</h2>', '<h3><span id="Story">Story</span></h3>'):
            with self.subTest(heading=heading):
                html = f"""
                    <h3><span id="AI_Description">AI Description</span></h3>
                    <p>Level 65 Neighborhood Boss!</p>
                    {heading}
                    <p>This belongs to another section.</p>
                """
                self.assertEqual(ai_description_paragraph_from_html(html), "")

    def test_ai_fallback_respects_skipped_containers(self) -> None:
        for container in (
            '<div class="mw-collapsible">',
            '<div class="dcc-highlight">',
            '<div class="gallery">',
        ):
            with self.subTest(container=container):
                html = f"""
                    <h2><span id="AI_Description">AI Description</span></h2>
                    {container}<blockquote><p>Hidden spoiler text.</p></blockquote></div>
                    <blockquote><p>The visible explanation.</p></blockquote>
                """
                self.assertEqual(ai_description_paragraph_from_html(html), "The visible explanation.")

    def test_ai_heading_inside_skipped_container_does_not_change_section(self) -> None:
        html = """
            <aside><h2><span id="AI_Description">AI Description</span></h2></aside>
            <p>A regular intro.</p>
        """
        self.assertEqual(ai_description_paragraph_from_html(html), "")
        self.assertEqual(summary_blocks_from_html(html), ["A regular intro."])

    def test_fallback_skips_quote_introductions_and_presentation_notes(self) -> None:
        labels = (
            "As part of the description for Levi the 7th:",
            "From the description of Ralph:",
            "Part of the Pueblo de los Olvidados Quest description:",
            "Section of AI Description of Krakaren Clone (Second Floor):",
            "Description of Yago, the Monk Seal Picket Sentry",
            "See also: Description for Lusca",
            "Of Quint, the drug dealer:",
            'The AI reads the description in a "caveman voice."',
            "The description is presented in a television commercial voice:",
            "New Achievement. Bomb's Away!",
            "-",
            "Boom.",
        )
        for label in labels:
            with self.subTest(label=label):
                html = f"""
                    <h2><span id="AI_Description">AI Description</span></h2>
                    <p>{label}</p>
                    <blockquote><p>A useful explanation of the subject.</p></blockquote>
                """
                self.assertEqual(ai_description_paragraph_from_html(html), "A useful explanation of the subject.")

    def test_descriptive_prose_is_not_confused_with_a_label(self) -> None:
        prose = "Part of a collective mind, these creatures share their knowledge."
        html = f'<h2 id="AI_Description">AI Description</h2><p>{prose}</p>'
        self.assertEqual(ai_description_paragraph_from_html(html), prose)

    def test_nested_italics_do_not_make_a_bold_header_prose(self) -> None:
        html = """
            <h2><span id="AI_Description">AI Description</span></h2>
            <blockquote>
                <p><b>Gnomish Legate Balloon. <i>The Vahana.</i> Contraption.</b></p>
                <p>The balloon was once used for peaceful journeys.</p>
            </blockquote>
        """
        self.assertEqual(ai_description_paragraph_from_html(html), "The balloon was once used for peaceful journeys.")

    def test_identity_header_can_supply_context_for_short_prose(self) -> None:
        html = """
            <p><b>Chiyome</b> is</p>
            <h2 id="AI_Description">AI Description</h2>
            <blockquote>
                <p><b>Chiyome. Razor Fox. Level 80 Mistress of Nunchaku.</b></p>
                <p>One of three from team The Wild Hunt.</p>
            </blockquote>
        """
        self.assertEqual(
            summary_from_html("Chiyome", html),
            "<b>Chiyome. Razor Fox. Level 80 Mistress of Nunchaku.</b> One of three from team The Wild Hunt.",
        )

    def test_identity_header_is_not_kept_when_it_only_adds_a_level(self) -> None:
        html = """
            <p><b>Sharp-Elbows</b> is</p>
            <h2 id="AI_Description">AI Description</h2>
            <blockquote><p><b>Sharp-Elbows. Level 80.</b></p><p>This is a necromancer.</p></blockquote>
        """
        self.assertEqual(summary_from_html("Sharp-Elbows", html), "This is a necromancer.")

    def test_sierra_keeps_identity_and_skips_level_only_paragraph(self) -> None:
        html = """
            <p>Sierra is</p>
            <h2 id="AI_Description">AI Description</h2>
            <blockquote>
                <p><b>Sierra - Northropi Vampire</b></p>
                <p>Level 65 Neighborhood Boss!</p>
                <p>This is a Bereft Minion of Viscount Fog.</p>
            </blockquote>
        """
        self.assertEqual(
            summary_from_html("Sierra", html),
            "<b>Sierra - Northropi Vampire</b>. This is a Bereft Minion of Viscount Fog.",
        )

    def test_wheeled_bomb_identity_and_type_survive_when_no_prose_exists(self) -> None:
        title = "Just Wait Until Your Daddy Gets Home"
        html = f"""
            <p>{title} is an item</p>
            <h2 id="AI_Description">AI Description</h2>
            <blockquote>
                <p><b>{title} – Wheeled Bomb.</b></p>
                <p>Type: High Yield Thermobaric Explosive. Also imbues Fear within blast radius.</p>
                <p>Effect: I hope you're paid up on your homeowner's insurance.</p>
                <p>Status: 250. Fortified.</p><p>Boom.</p>
            </blockquote>
        """
        self.assertEqual(
            summary_from_html(title, html),
            f"<b>{title} – Wheeled Bomb.</b> Type: High Yield Thermobaric Explosive. Also imbues Fear within blast radius.",
        )

    def test_stat_only_ai_section_does_not_replace_generic_intro(self) -> None:
        html = """
            <p>Ping Spell is a spell.</p>
            <h2 id="AI_Description">AI Description</h2>
            <blockquote>
                <p><b>Ping Spell</b></p><p>Ping Spell.</p>
                <p>Cost: 5 Mana</p><p>Level 2.</p><p>Reward: A box.</p>
                <p>Warning: Watch out!</p><p>Status: Ready.</p>
            </blockquote>
        """
        self.assertEqual(summary_from_html("Ping Spell", html), "Ping Spell is a spell.")

    def test_ai_replacement_does_not_append_surrounding_dialogue(self) -> None:
        html = """
            <p>Sharp-Elbows is</p>
            <h2 id="AI_Description">AI Description</h2>
            <blockquote><p>Basically a necromancer.</p></blockquote>
            <p>Carl asked the AI for a longer description.</p>
        """
        self.assertEqual(summary_from_html("Sharp-Elbows", html), "Basically a necromancer.")

    def test_ai_identity_does_not_override_better_prose_or_length_limit(self) -> None:
        prose = "This spell tracks a target. " + "It continues to track the target across the battlefield. " * 4
        html = f"""
            <p>Ping Spell is a spell.</p>
            <h2 id="AI_Description">AI Description</h2>
            <blockquote><p><b>Ping Spell - Tracking spell.</b></p><p>{prose}</p></blockquote>
        """
        self.assertEqual(summary_from_html("Ping Spell", html, 50), "This spell tracks a target.")


if __name__ == "__main__":
    unittest.main()
