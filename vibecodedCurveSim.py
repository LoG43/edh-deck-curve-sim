#!/usr/bin/env python3
"""
vibecodedCurveSim.py
=======================================================================
Monte Carlo simulator that estimates, for every NONLAND card in a
Magic: The Gathering Commander (EDH) decklist -- mana rocks, mana
dorks, ramp spells, and ordinary action spells alike -- the
probability that the deck's MANA BASE (not the card's own draw odds)
can pay that card's cost on a given turn. This is a mana-availability
question, not a "will I have drawn this card by then" question (see
CHANGELOG Pass 7): the simulator checks a card's cost against each
game's actual, hand-driven mana pool on every turn in a window
starting at its CMC and running MANA_AVAILABILITY_LOOKAHEAD turns
past it, REGARDLESS of whether that specific card was drawn into hand
that game. It's given:

    * the deck's actual land count and mana curve (drawn from a real
      shuffled 99/100-card library),
    * mana rocks, mana dorks, and land-ramp spells in the deck, which
      accelerate how much mana is available on a given turn (their
      OWN costs are checked the same mana-availability way as any
      other nonland card), and
    * a mulligan rule: a simulated player will not keep an opening
      hand unless that hand, on its own (before any further draws),
      would let them cast at least two nonland spells within the
      first few turns. Hands that fail this test are mulliganed
      (London mulligan: draw 7, keep, then put N cards on the bottom
      of the library where N = number of mulligans already taken).
      This part -- land sequencing and the mulligan decision -- is
      still driven by each game's ACTUAL hand; only the reported
      per-card statistic is decoupled from whether that card itself
      was in it.

Card data (mana cost, type line, oracle text, colors, etc.) is fetched
from the Scryfall API via the `scrython` library
(https://github.com/NandaScott/Scrython) and cached to a local JSON
file so repeat runs don't hammer Scryfall's servers.

--------------------------------------------------------------------
WHY A SIMULATION INSTEAD OF A CLOSED-FORM FORMULA?
--------------------------------------------------------------------
A plain hypergeometric calculation ("what's the chance I've drawn N
lands by turn T") can't easily account for:
    - ramp spells conditionally adding mana sources on *future* turns,
    - mana rocks that come online immediately (no summoning sickness
      for artifacts) versus mana dorks that come online a turn later
      (creatures DO have summoning sickness),
    - colored-mana requirements (a card needing UU isn't "castable"
      just because 2 total mana is open),
    - the mulligan decision itself, which changes the distribution of
      what ends up in the library.
A turn-by-turn Monte Carlo simulation handles all of this naturally:
we just simulate thousands of games and count outcomes.

--------------------------------------------------------------------
KEY SIMPLIFYING ASSUMPTIONS (read this before trusting the numbers!)
--------------------------------------------------------------------
This is a statistical *approximation* of deck performance, not a full
rules-accurate MTG game engine. In particular:

 1. MANA ROCKS (nonland, noncreature artifacts with a tap-for-mana
    ability) are usable the SAME turn they are cast. This matches the
    actual rules: summoning sickness (rule 302.6) only restricts
    CREATURES, not artifacts. If the tap ability bundles an ADDITIONAL
    COST alongside the tap symbol (e.g. a guild Signet's "{1}, {T}:
    Add {U}{B}."), the rock/dork is credited with its NET mana --
    mana added minus the extra cost paid, so a Signet nets +1 mana
    (two colored mana produced, one generic spent), not a phantom +2
    for free (see `_tap_ability_mana_count`, Pass 17). Abilities with
    no extra cost (Sol Ring, Arcane Signet, Mind Stone, ...) are
    unaffected -- this only changes anything for the subset of rocks/
    dorks whose ability text pays for itself out of your other mana.

 2. MANA DORKS (creatures with a tap-for-mana ability) are usable
    starting the turn AFTER they are cast, unless their oracle text
    contains the keyword "Haste".

 3. RAMP SPELLS that search for/put a land onto the battlefield
    (e.g. Rampant Growth, Cultivate, Nature's Lore) are assumed to put
    that land onto the battlefield TAPPED, which is the common case
    for budget/midrange ramp. That land is therefore usable starting
    the turn AFTER the ramp spell resolves. (Toggle
    ASSUME_RAMP_LANDS_TAPPED near the top of the file if your list
    leans on untapped ramp like Skyshroud Claim / Farseek-into-duals.)

 4. Only ONE ramp/mana-rock spell is assumed to be deployed per turn
    (the cheapest affordable one), mirroring typical sequencing where
    a player plays a land and one accelerant per turn. EVERY nonland
    card in the decklist (as of Pass 7: including that turn's own
    accelerant candidates, not just "action" spells) is checked for
    mana availability against whatever pool is left over that turn,
    independently of one another and independently of whether the
    card being checked was actually drawn into hand. This means the
    tool answers "did my mana base have what this cost needed," not
    "did the simulated player's exact chosen play line definitely
    include casting this exact card." For curve/mana-base analysis
    this is the standard and useful framing.

 5. Colored-mana requirements are resolved with a greedy
    "most-constrained-source-first" matcher (see `can_pay_cost`).
    It is a good approximation of real deckbuilding math but is not
    an exhaustive constraint solver, so extremely convoluted mana
    bases may be mismodeled at the margins.

 6. Land drops: exactly one land per turn, chosen (when there's a
    choice) to best satisfy the colors needed by the cheapest
    not-yet-castable NONLAND card in hand -- mana rocks, mana dorks,
    and ramp spells compete for this on equal footing with ordinary
    action spells (Pass 15), not a separate priority tier: a card is
    "cheapest" purely by cmc, whichever category it's in.

 7. The commander itself is assumed to be cast from the command zone
    and is excluded from the simulated library -- put it under an
    explicit "Commander" heading in your decklist file (see
    `parse_decklist`) and it will automatically be excluded.

 8. Double-faced cards (MDFCs, transform, and other card_faces-bearing
    layouts) are read from a single chosen FACE, never the top-level
    fields (which can be a "{front} // {back}" concatenation of both
    faces on some layouts -- see CHANGELOG Pass 9). Which face: if
    EITHER face is a land, the card is modeled as that land ONLY --
    its spell face is ignored entirely for curve purposes (Pass 10;
    this is a deliberate design decision, reversed from an earlier one
    in this same project). Otherwise, the front face (card_faces[0])
    is used, e.g. a transform creature with no land face at all.

 9. FETCHLANDS (lands with a "Sacrifice ~: Search your library for a
    ... card, put it onto the battlefield" ability) are parsed at
    classification time into `is_fetch_land` / `fetch_kind` /
    `fetch_subtypes` / `fetch_count` / `fetch_forces_tapped` (see the
    `Card` dataclass and `_parse_fetch_land`), and ARE fully resolved
    into real mana by the engine's land-drop step (`resolve_fetch`,
    called from `play_land_drop`, STEP 5): the fetchland is sacrificed
    the same turn it's played, a legal target is searched for and
    REMOVED from the simulated library (real deck-thinning), and a
    `ManaSource` is added for whatever land was actually found -- using
    that land's own colors/tapped-state, not the fetchland's own
    (still-empty) `produced_colors`. A fetch that whiffs (no legal
    target left) is still sacrificed for nothing, same as in a real
    game. Fetch-target selection also prefers whichever color the
    cheapest not-yet-castable spell in hand needs (assumption 6).

10. ENTERS-TAPPED LANDS (unconditional taplands, shocklands, and the
    conditional cycles -- slow/fast/bond/check/battle lands) are
    parsed into `tapped_kind` / `check_types` (see `_parse_tapped_kind`),
    and ARE fully resolved against the actual battlefield by
    `resolve_tapped_state` (STEP 5) every time a land enters play --
    including a land found by a fetch, which is checked against the
    same real battlefield state as an ordinarily-drawn land would be.

11. SCOPE: this tool only ever answers manabase/mana-curve questions
    ("could this spell be cast on curve"). Two things that would be
    needed for other kinds of analysis are deliberately NOT modeled,
    and are non-goals rather than open gaps:
      - Life totals. Fetchland cracks and shockland payments are not
        tracked at all -- the mana is assumed to just happen. This
        would matter for burn-matchup / life-total-sensitive analysis,
        which is out of scope for a curve simulator.
      - Nonland permanents that enter tapped or have fetch-like
        effects (e.g. a creature that enters tapped, or a spell that
        sacrifices something to tutor a permanent onto the
        battlefield). `_parse_fetch_land`/`_parse_tapped_kind` are
        only ever invoked for lands (see `classify_card`'s `is_land`
        guard); mana rocks and mana dorks keep their existing,
        separate same-turn/next-turn modeling (assumptions 1-2 above)
        and are unaffected by this.

12. EXACTLY ONE CARD IS DRAWN PER TURN (see `run_single_game`, STEP 6),
    every turn including turn 1 (assumption 7's Commander-specific
    multiplayer draw rule). Card-draw spells and effects (Sign in
    Blood, Faithless Looting, wheel effects, Rhystic Study triggers,
    etc.) are NOT modeled -- the simulated player never sees extra
    cards beyond the one-per-turn baseline, regardless of what's
    actually in the decklist. This is a real, and potentially large,
    source of pessimism: a deck full of card advantage will find its
    lands and action spells meaningfully faster in an actual game than
    this tool's numbers suggest, because seeing more cards raises the
    odds of having the right land or accelerant in hand by any given
    turn. There is no toggle for this -- it is a structural limitation
    of the one-card-per-turn draw loop, not a configurable assumption.

13. THE HEADLINE RELIABILITY FIGURE IS PAIRED WITH A STANDARD DEVIATION
    (`_summary_deck_stddev`), not just a mean. The mean alone can hide
    a deck where a handful of very-reliable colorless rocks prop up
    the average while most colored spells sit well below it; the
    stddev is computed with the identical quantity-weighting and
    per-offset population as the mean itself, so the two numbers
    describe the same underlying set of cards.

14. MANA SCREW/FLOOD RATES (`SCREW_CHECK_TURN`/`SCREW_LAND_THRESHOLD`,
    `FLOOD_CHECK_TURN`/`FLOOD_EXCESS_LAND_THRESHOLD`, resolved in
    `run_simulation`) are fixed checkpoints, not adaptive to the
    deck's own curve. SCREWED means "SCREW_LAND_THRESHOLD (4) or fewer
    real lands in play (see `_battlefield_lands` -- ramp-spell lands
    with no backing Card don't count) by SCREW_CHECK_TURN (turn 6)" --
    i.e. missed at least two of the first six land drops, which is a
    real, game-warping outcome for a 36+ land deck rather than
    ordinary variance. FLOODED means "FLOOD_EXCESS_LAND_THRESHOLD (2)
    or more lands sitting unplayed in hand by FLOOD_CHECK_TURN (turn
    6)." Both are deck-wide outcomes tracked per SIMULATED GAME (not
    per card), reported as a single fraction of games that hit the
    threshold. If `--max-turns`/`max_turns` is shorter than the
    relevant checkpoint turn, that rate is reported as unmeasured
    (None) rather than a misleading 0%.

15. CREATURE- AND ARTIFACT-BASED LAND RAMP IS NOT MODELED AS RAMP AT
    ALL -- this is a real, deliberate scope gap, not an oversight,
    and it is significant enough to call out on its own rather than
    leave buried in CHANGELOG history (Pass 11). `is_ramp_spell`
    (see `classify_card`) requires `not is_artifact and not
    is_creature`: only sorceries/instants that search for a land and
    put it onto the battlefield are ever classified as ramp (assumption
    3 above). Common EDH ramp pieces that fetch a land from a CREATURE
    or ARTIFACT source -- Solemn Simulacrum, Wayfarer's Bauble,
    Burnished Hart, Sword of the Animist, and similar cards -- are
    therefore classified as plain `is_action_spell` cards instead:
    only their OWN casting cost is checked for mana availability, and
    the land they would have put into play contributes NOTHING to the
    simulated mana base. This is not merely conservative the way most
    of this tool's approximations are -- the card's entire ramp effect
    is invisible to the simulation, not just discounted. A decklist
    that leans on this style of ramp will show a systematically
    thinner, slower-developing mana base than it would in a real game.
    There is no toggle for this; fixing it would mean recognizing land-
    search-and-battlefield triggers on creatures/artifacts as a new
    accelerant category, which hasn't been built.

None of these assumptions are exotic -- they mirror how experienced
deckbuilders reason about curves by hand -- but they are worth
knowing about before you treat the output as gospel.

--------------------------------------------------------------------
CHANGELOG
--------------------------------------------------------------------
The pass-by-pass development history (Pass 2 through Pass 15) has
moved to CHANGELOG.md in the repository root, to keep this docstring
shorter -- it is a historical record, not living documentation (see
that file's own header for what that means).

--------------------------------------------------------------------
DECKLIST FILE FORMAT
--------------------------------------------------------------------
Plain text, one card per line, quantity first:

    1 Sol Ring
    1x Command Tower
    38 Forest
    1 Rampant Growth

Lines starting with '#' or '//' are treated as comments and ignored.
An optional "Commander" section header (its own line, optionally
followed by a colon) marks the following card(s) as commander(s) --
they are excluded from the simulated 99-card library:

    Commander
    1 Atraxa, Praetors' Voice

    Deck
    1 Sol Ring
    ...

Common deck-export suffixes like set code / collector number
("(C21) 263") are stripped automatically.

--------------------------------------------------------------------
USAGE
--------------------------------------------------------------------
    pip install scrython
    python vibecodedCurveSim.py my_deck.txt
    python vibecodedCurveSim.py my_deck.txt --simulations 20000 --csv out.csv

Run `python vibecodedCurveSim.py --help` for all options.
"""

# =======================================================================
# IMPORTS
# =======================================================================
import argparse            # command-line argument parsing
import math                  # weighted standard deviation (STEP 8)
import random                # shuffling the simulated library, drawing cards
import re                     # pattern matching over card oracle text
import sys                     # exiting cleanly with error messages
import csv                      # optional CSV export of results
from collections import defaultdict   # convenient default-valued dicts
from dataclasses import dataclass, field  # clean, typed card records
from pathlib import Path                   # filesystem paths
from typing import Optional, Callable        # optional type hints

# Scryfall fetching/caching lives in its own module (see CHANGELOG.md)
# -- this file only ever calls the three functions below plus _emit
# (reused here for _build_card_lookup's own "not found" warning).
from scryfall_client import load_cache, save_cache, fetch_card_data, _emit


# =======================================================================
# CONFIGURATION CONSTANTS
# (Tunable knobs. Command-line flags override some of these; the rest
#  are "we had to pick something" modeling choices explained above.)
# =======================================================================

# Whether ramp spells that fetch a land are assumed to put it into play
# tapped (True, the common/conservative case) or untapped (False).
ASSUME_RAMP_LANDS_TAPPED = True

# Standard MTG opening hand size and the mulligan rule (London mulligan:
# always draw back up to 7, then put N cards on the bottom of the
# library, where N is the number of mulligans already taken).
OPENING_HAND_SIZE = 7

# We won't mulligan forever -- real players stop somewhere. This caps
# the number of mulligans a simulated player will take before just
# keeping whatever they have (bottoming cards as normal for whichever
# mulligan count they stopped at).
MAX_MULLIGANS = 3

# A hand is considered "keepable" if, using ONLY the cards in that
# hand (no future draws), the player could have cast at least this
# many *distinct* nonland spells by MULLIGAN_LOOKAHEAD_TURNS.
MIN_SPELLS_TO_KEEP = 2
MULLIGAN_LOOKAHEAD_TURNS = 4

# How many turns a single simulated game plays out. Curve stats are
# only ever reported for cards with cmc in [1, MAX_SIMULATED_TURNS];
# anything above that never gets checked (see run_simulation).
MAX_SIMULATED_TURNS = 10

# Mana screw / flood detection (STEP 6b). These are deliberately fixed
# checkpoints, not adaptive to the deck's own curve -- a single
# well-justified, transparent definition is more useful for comparing
# decks against each other than a "smart" one that moves the goalposts
# per deck. Both are documented on the web app's "how this simulator
# thinks" page.
#
# SCREWED: by SCREW_CHECK_TURN, the player has SCREW_LAND_THRESHOLD or
# fewer lands actually in play (see _battlefield_lands -- ramp-spell
# lands with no backing Card don't count, matching how SLOW/FAST/
# BATTLE lands already treat "lands in play" elsewhere in this file).
# Turn 6 with a 4-or-fewer threshold means "missed at least two of
# your first six land drops" -- a real, game-warping outcome, not
# ordinary variance (missing zero or one drop by turn 6 is the norm
# for a 36+ land deck).
SCREW_CHECK_TURN = 6
SCREW_LAND_THRESHOLD = 4

# FLOODED: by FLOOD_CHECK_TURN, the player is holding
# FLOOD_EXCESS_LAND_THRESHOLD or more lands that are still sitting
# unplayed in hand (i.e. lands drawn but not yet played, since only
# one land can be played per turn). Turn 6 gives a flood-prone deck
# enough time for redundant lands to visibly pile up in hand.
FLOOD_CHECK_TURN = 6
FLOOD_EXCESS_LAND_THRESHOLD = 2

# Regex used to spot "this permanent taps for mana" abilities in oracle
# text, e.g. "{T}: Add {C}." or "{T}: Add one mana of any color."
TAP_FOR_MANA_RE = re.compile(r"\{T\}[^.]*:\s*Add\b", re.IGNORECASE)

# Strips parenthetical reminder text before TAP_FOR_MANA_RE is checked.
# Scryfall wraps a TOKEN's granted ability in parentheses when a card's
# own text creates/describes that token (e.g. Pitiless Plunderer: "...
# create a Treasure token. (It's an artifact with '{T}, Sacrifice this
# token: Add one mana of any color.')") -- without stripping this,
# TAP_FOR_MANA_RE matches the TOKEN's ability and misclassifies the
# CARD ITSELF (which never taps for mana) as a mana dork/rock. Verified
# safe: every genuine mana rock/dork across the decks this tool has
# processed also states its ability outside parentheses, so this only
# ever removes false positives, never a true one.
PAREN_RE = re.compile(r"\([^)]*\)")

# Captures a full "{T}: Add ..." tap ability in two pieces: group(1) is
# everything in the activation cost (bounded by '.'/':' so it never
# crosses into a different ability or sentence), group(2) is the mana
# symbols after "Add". Used by _tap_ability_mana_count to net out any
# EXTRA cost bundled alongside the tap (e.g. a guild Signet's "{1}, {T}:
# Add {U}{B}." pays {1} to net {U}{B}) rather than crediting the full
# "Add" output as if the ability were free -- see Pass 17.
TAP_ABILITY_RE = re.compile(
    r"([^.:]*\{T\}[^.:]*):\s*Add\s+((?:\{[^}]+\})+)",
    re.IGNORECASE,
)

# Regex used to spot land-SEARCH clauses, e.g. "search your library for
# a basic land card". Matches on the search alone -- NOT sufficient by
# itself to conclude the card is a ramp spell, since "search your
# library for a land card" is also how a card that puts land(s) into
# your HAND (not the battlefield) reads (e.g. Prismatic Undercurrents:
# "...put them into your hand..."). Use is_ramp_spell (which additionally
# requires _ramp_puts_onto_battlefield) to actually classify a card as
# ramp; use this regex alone only for extracting the fetch count, where
# the destination doesn't matter.
LAND_RAMP_RE = re.compile(
    r"search your library for (?:a|one|two|three|up to \w+) .*?land card",
    re.IGNORECASE,
)

# Words-to-numbers used to figure out how many lands a ramp spell fetches.
_NUMBER_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}

# Regex to pull mana symbols (e.g. "{2}{U}{U}") out of a mana cost string.
MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")

# Recognized colors (W/U/B/R/G) plus colorless (C). Colorless mana can
# pay generic costs but never satisfies a colored pip requirement.
COLOR_LETTERS = {"W", "U", "B", "R", "G"}

# Fetchland detection: "Sacrifice ~: Search your library for <target>
# card(s), put it/them onto the battlefield[ tapped]". Deliberately
# anchored on the "Sacrifice ...:" activated-cost prefix so this never
# matches a land-ramp SPELL (Cultivate, etc.) -- those are caught by
# LAND_RAMP_RE instead and are mutually exclusive from fetchlands
# because is_ramp_spell requires `not is_land`.
FETCH_RE = re.compile(
    r"Sacrifice [^:]*?:\s*Search your library for ([^,]+?)\s+cards?,"
    r"\s*put (?:it|them) onto the battlefield( tapped)?",
    re.IGNORECASE,
)

# Shocklands: "you may pay 2 life. If you don't, it enters tapped."
# Checked before the generic tapped regexes below so shocks don't get
# misclassified as unconditionally tapped -- we assume the life is
# paid, so shocks resolve to "UNTAPPED" (see assumption 3 above; life
# totals aren't tracked at all, per assumption 11's scope).
SHOCK_RE = re.compile(
    r"you may pay \d+ life\.\s*If you don't,\s*it enters tapped",
    re.IGNORECASE,
)

# Conditional taplands: "enters the battlefield tapped unless <condition>."
# Captures the condition clause so _parse_tapped_kind can classify it
# (slow/fast/bond/battle/check -- see _parse_tapped_kind's own docstring).
TAPPED_UNLESS_RE = re.compile(
    r"enters(?: the battlefield)? tapped unless (.+?)\.",
    re.IGNORECASE,
)

# Unconditional taplands (guildgates, temples, karoo lands, ...):
# "enters the battlefield tapped." with no "unless" clause. Checked
# only after TAPPED_UNLESS_RE and SHOCK_RE have had a chance to match,
# since both of those also contain the substring "enters ... tapped".
TAPPED_UNCONDITIONAL_RE = re.compile(
    r"enters(?: the battlefield)? tapped\.",
    re.IGNORECASE,
)


# =======================================================================
# DATA MODEL
# =======================================================================
@dataclass
class Card:
    """
    A single unique card in the decklist, with everything the
    simulator needs to know about how/when it can be played.
    """
    name: str
    quantity: int                 # copies in the deck (basic lands can be >1)
    cmc: int                      # converted mana cost / mana value
    mana_cost: str                 # raw cost string, e.g. "{1}{U}{U}" -- cmc alone
                                    # can't tell can_pay_cost what COLORS are needed
    type_line: str
    oracle_text: str
    colors: frozenset             # colors printed on the card (for reference)
    color_identity: frozenset

    # Cosmetic only -- never read by the simulation engine itself, just
    # by the web app's background-art feature (app.py). Scryfall's
    # "art crop" is the card's illustration with the frame/text box
    # cropped out, from whichever face classify_card chose to model
    # this card as (see the face-precedence comment in classify_card).
    art_crop_url: str = ""

    is_land: bool = False
    is_mana_rock: bool = False    # nonland, noncreature artifact that taps for mana
    is_mana_dork: bool = False    # creature that taps for mana
    is_ramp_spell: bool = False   # sorcery/instant that fetches land(s)

    # How many individual mana "instances" a single tap of this
    # permanent NETS (Sol Ring = 2, a basic land = 1, a guild Signet
    # like Boros Signet = 1 net -- 2 produced minus the {1} its own
    # ability costs to activate; see _tap_ability_mana_count, Pass 17).
    mana_per_tap: int = 1

    # What colors of mana this source can produce. For colorless-only
    # sources (Sol Ring, Mind Stone) this is frozenset({"C"}).
    produced_colors: frozenset = field(default_factory=frozenset)

    # For ramp spells only: how many lands they fetch, and whether the
    # fetched land(s) enter tapped (delaying availability by a turn).
    lands_fetched: int = 1
    fetched_lands_tapped: bool = ASSUME_RAMP_LANDS_TAPPED

    # --- Fetchland fields ------------------------------------------------
    # A fetchland's own `produced_colors` is deliberately left empty
    # (see is_land branch of classify_card) -- it produces no mana by
    # itself. These fields describe what it can put onto the
    # battlefield instead; `resolve_fetch` (STEP 5) is the land-drop-time
    # code that actually resolves that into a real ManaSource.
    is_fetch_land: bool = False
    fetch_kind: str = ""            # "" | "BASIC" | "TYPED" | "ANY"
    fetch_subtypes: frozenset = field(default_factory=frozenset)  # e.g. {"Island","Swamp"} for TYPED
    fetch_count: int = 1             # Krosan Verge-style multi-fetches; see known-limitations notes
    fetch_forces_tapped: bool = False   # Evolving Wilds, Fabled Passage

    # --- Tapped-land fields ------------------------------------------------
    # "UNTAPPED" (default -- includes shocklands, life assumed paid),
    # "ALWAYS" (guildgates, temples, karoo lands), or the conditional
    # cycles "SLOW"/"FAST"/"BOND"/"BATTLE"/"CHECK". Only "CHECK" uses
    # check_types (the land subtypes named in its own oracle text, e.g.
    # {"Plains","Island"} for Glacial Fortress). Evaluating a condition
    # against the actual battlefield happens at land-drop time, not here.
    tapped_kind: str = "UNTAPPED"
    check_types: frozenset = field(default_factory=frozenset)

    @property
    def is_mana_source(self) -> bool:
        """True for anything that can produce mana when tapped."""
        return self.is_land or self.is_mana_rock or self.is_mana_dork

    @property
    def is_action_spell(self) -> bool:
        """
        A "normal" nonland spell we want curve statistics for --
        i.e. everything that isn't a land, mana rock, mana dork, or
        land-ramp spell. (Ramp pieces get their own bookkeeping since
        they change the mana base rather than just doing something
        once when cast.)
        """
        return not (self.is_land or self.is_mana_rock or self.is_mana_dork
                    or self.is_ramp_spell)


@dataclass
class ManaSource:
    """
    One permanent that is currently in play and can produce mana.
    `ready` is False for the turn a mana dork enters (summoning
    sickness) or the turn a tapped ramp-fetched land enters; it flips
    to True automatically on the next turn.
    """
    colors: frozenset      # e.g. frozenset({"G"}) or frozenset({"C"})
    amount: int            # mana instances produced per tap (usually 1)
    ready: bool            # can it be tapped THIS turn?

    # Back-reference to the originating Card.
    # Needed by land-drop logic that has to ask questions about the
    # permanent's identity rather than just the mana it makes -- e.g.
    # "is this land a Plains" for a CHECK-kind tapland's condition, or
    # "does the battlefield have two or more basic lands" for a
    # BATTLE-kind one. Without this, ManaSource threw that identity
    # away and those questions were unanswerable.
    card: Optional["Card"] = None


# =======================================================================
# STEP 1: PARSE THE DECKLIST TEXT FILE
# =======================================================================
def parse_decklist(path: Path):
    """
    Read a plain-text decklist and return (commander_names, library_counts)
    where library_counts is a dict of {card_name: quantity} for every
    card that goes in the simulated 99-card library (i.e. everything
    except any card(s) listed under a "Commander" heading).
    """
    commander_names = []
    library_counts: dict[str, int] = defaultdict(int)

    # Matches "<qty> <name>" optionally followed by a set-code/collector
    # number suffix like "(C21) 263" that deckbuilding sites often add.
    line_re = re.compile(
        r"^\s*(\d+)\s*x?\s+(.+?)\s*(?:\([A-Za-z0-9]+\)\s*[\w-]*\s*)?$"
    )

    in_commander_section = False
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            if not line or line.startswith("#") or line.startswith("//"):
                continue  # blank line or comment

            # Section headers: "Commander", "Commanders", "Deck",
            # "Deck:", "Sideboard", etc. We only special-case Commander;
            # everything else just resets us back to "library" mode
            # (this also lets a plain "Deck" header be a no-op).
            header = line.rstrip(":").strip().lower()
            if header in ("commander", "commanders"):
                in_commander_section = True
                continue
            if header in ("deck", "decklist", "library", "mainboard", "main"):
                in_commander_section = False
                continue
            if header in ("sideboard", "maybeboard", "maybe board"):
                # We don't simulate the sideboard/maybeboard; stop
                # reading entirely once we hit one of these headers.
                break

            match = line_re.match(line)
            if not match:
                # Doesn't look like "<qty> <name>" -- skip it rather
                # than crash, but let the user know.
                print(f"  [parse_decklist] Skipping unrecognized line: {line!r}",
                      file=sys.stderr)
                continue

            qty = int(match.group(1))
            name = match.group(2).strip()

            if in_commander_section:
                commander_names.append(name)
            else:
                library_counts[name] += qty

    if not library_counts:
        raise ValueError(f"No library cards were parsed from {path}. "
                          f"Check the file format (see the module docstring).")

    return commander_names, dict(library_counts)


# =======================================================================
# STEP 3: CLASSIFY EACH CARD (land / mana rock / mana dork / ramp / action)
# =======================================================================
def _extract_mana_symbols(cost_str: str) -> list:
    """Turn '{2}{U}{U}' into ['2', 'U', 'U']."""
    if not cost_str:
        return []
    return MANA_SYMBOL_RE.findall(cost_str)


def _tap_ability_mana_count(oracle_text: str) -> int:
    """
    Estimate how many mana instances a single activation of a tap-for-
    mana ability nets, by looking at the symbols right after "Add" in
    the "{T}: Add" ability, MINUS any additional cost bundled into that
    same activation alongside the tap symbol (e.g. a guild Signet's
    "{1}, {T}: Add {U}{B}." pays {1} to net {U}{B} -- 2 mana produced,
    1 spent, net +1).

    Before Pass 17 this only looked at the "Add" side, so a Signet-
    style rock was credited with its full 2-mana output for free every
    turn, as if the ability had no cost at all -- overstating available
    mana for any deck running this common EDH staple (Sol Ring, Arcane
    Signet, Mind Stone, and other cost-free rocks were unaffected,
    since their abilities have nothing else to subtract). This is the
    one place in the file that previously erred OPTIMISTIC rather than
    pessimistic (see CHANGELOG Pass 12's "err pessimistic" audit, which
    predates this fix and didn't cover fixed extra costs).

    Defaults to 1 if we can't parse a "{T}: Add" ability at all (true
    for the vast majority of mana rocks/dorks/lands, which produce
    exactly one mana per tap for no additional cost). Floors the net at
    0 rather than 1 for a card whose activation cost genuinely exceeds
    its own output -- no real mana rock/dork does this, but a floor of
    0 is more honest than a phantom minimum of 1 mana for one that did.
    """
    match = TAP_ABILITY_RE.search(oracle_text or "")
    if not match:
        return 1
    cost_text, added_text = match.group(1), match.group(2)

    # Symbols like "{C}{C}" -> 2 instances. A symbol that's a number
    # (rare here, but defensive) would mean "add N mana of any type" --
    # treat that as N instances too.
    added = sum(int(sym) if sym.isdigit() else 1 for sym in _extract_mana_symbols(added_text))

    # Everything in the activation cost EXCEPT the tap symbol itself
    # counts against that net -- generic numbers at face value, any
    # other symbol (a colored pip, {C}, etc.) as 1, same convention as
    # the "added" side above.
    paid = sum(int(sym) if sym.isdigit() else 1
               for sym in _extract_mana_symbols(cost_text) if sym != "T")

    return max(added - paid, 0)


def _lands_fetched_count(oracle_text: str) -> int:
    """Best-effort parse of how many lands a ramp spell searches for."""
    match = LAND_RAMP_RE.search(oracle_text or "")
    if not match:
        return 1
    # Look at the word immediately before "land card" in the match.
    words = match.group(0).lower().split()
    for word in reversed(words):
        if word in _NUMBER_WORDS:
            return _NUMBER_WORDS[word]
    return 1


def _ramp_puts_onto_battlefield(oracle_text: str) -> bool:
    """
    True if a LAND_RAMP_RE land-search clause actually puts the found
    land(s) onto the BATTLEFIELD, not just into hand. LAND_RAMP_RE
    alone can't tell these apart -- "search your library for a land
    card" reads identically whether the card says "...put it onto the
    battlefield" (Rampant Growth: real ramp) or "...put it into your
    hand" (Prismatic Undercurrents: land tutor/fixing, not ramp; it
    doesn't accelerate the battlefield at all). Checked by scanning the
    sentence containing the match (up to the next period) for
    "battlefield" -- wide enough to cover Cultivate-style split text
    ("put one onto the battlefield tapped and the other into your
    hand") without crossing into an unrelated following sentence.
    """
    match = LAND_RAMP_RE.search(oracle_text or "")
    if not match:
        return False
    tail = oracle_text[match.end():]
    sentence_end = tail.find(".")
    sentence = tail if sentence_end == -1 else tail[:sentence_end]
    return "battlefield" in sentence.lower()


def _parse_fetch_land(oracle_text: str) -> Optional[dict]:
    """
    Detect a true fetchland ability ("Sacrifice ~: Search your library
    for ..., put it onto the battlefield[ tapped]") and return a dict
    of the Card.fetch_* fields to apply, or None if this isn't one.

    Known limitation: multi-target fetches with two independent search
    clauses (Krosan Verge: "a Forest card and a non-Forest land card")
    are not decomposed correctly -- FETCH_RE's capture group swallows
    both clauses as one blob, so fetch_subtypes ends up as whatever
    capitalized words happen to appear in it (here: {"Forest"}, which
    is only accurate for the first of the two lands it fetches).
    fetch_count is still right, via a "count the word card/cards"
    heuristic rather than parsing the capture group.
    """
    match = FETCH_RE.search(oracle_text or "")
    if not match:
        return None

    target_desc = match.group(1).strip()
    forces_tapped = bool(match.group(2))

    # Real land-type names are the only capitalized words that appear
    # mid-sentence in oracle text ("an Island or Swamp card"), so any
    # capitalized word here is a subtype to search for.
    named_types = re.findall(r"[A-Z][a-zA-Z]+", target_desc)
    if named_types:
        fetch_kind = "TYPED"
        fetch_subtypes = frozenset(named_types)
    elif "basic" in target_desc.lower():
        fetch_kind = "BASIC"
        fetch_subtypes = frozenset()
    else:
        fetch_kind = "ANY"
        fetch_subtypes = frozenset()

    # Heuristic multi-fetch count: count "card"/"cards" occurrences in
    # the whole matched ability text rather than trying to parse the
    # (possibly compound) target description word-for-word.
    fetch_count = max(len(re.findall(r"\bcards?\b", match.group(0), re.IGNORECASE)), 1)

    return dict(
        is_fetch_land=True,
        fetch_kind=fetch_kind,
        fetch_subtypes=fetch_subtypes,
        fetch_count=fetch_count,
        fetch_forces_tapped=forces_tapped,
    )


def _parse_tapped_kind(oracle_text: str) -> tuple:
    """
    Classify a land's enters-tapped behavior from its oracle text.
    Returns (tapped_kind, check_types) per the Card field docs above.

    Detection order matters: SHOCK_RE and TAPPED_UNLESS_RE are both
    checked before the unconditional TAPPED_UNCONDITIONAL_RE, because
    a shockland's "If you don't, it enters tapped" and a conditional
    tapland's "enters the battlefield tapped unless ..." both contain
    the substring the unconditional regex would otherwise match.

    Within the "unless" branch, order also matters: a battle land's
    condition ("two or more basic lands") contains "more", which would
    otherwise be misread as a slow land, so the "basic" check runs
    first. Known gap: this cannot tell a check land from any other
    land-based condition wording that doesn't hit one of the named
    cycles -- anything that reaches the final fallback is assumed to
    be a check land and have its condition's capitalized words parsed
    as basic land subtypes, which is correct for the real check-land
    cycle but not guaranteed for an oracle-text pattern nobody's
    designed yet.
    """
    text = oracle_text or ""

    if SHOCK_RE.search(text):
        return "UNTAPPED", frozenset()

    unless_match = TAPPED_UNLESS_RE.search(text)
    if unless_match:
        condition = unless_match.group(1)
        condition_lower = condition.lower()
        if "opponent" in condition_lower:
            return "BOND", frozenset()
        if "basic land" in condition_lower:
            return "BATTLE", frozenset()
        if "fewer" in condition_lower:
            return "FAST", frozenset()
        if "more" in condition_lower:
            return "SLOW", frozenset()
        # Fallback: check-land wording, e.g. "you control a Plains or
        # an Island". Basic land types are printed on type_line for
        # every land that has them (including non-basics like
        # shocklands), so this is answerable at land-drop time without
        # any further data than what Scryfall already gives us.
        check_types = frozenset(re.findall(r"\b(?:a|an)\s+([A-Z][a-zA-Z]+)", condition))
        return "CHECK", check_types

    if TAPPED_UNCONDITIONAL_RE.search(text):
        return "ALWAYS", frozenset()

    return "UNTAPPED", frozenset()


def classify_card(raw: dict, deck_colors: frozenset) -> Card:
    """
    Turn a raw Scryfall JSON dict into a classified Card object,
    figuring out whether it's a land, mana rock, mana dork, ramp
    spell, or a plain "action" spell we want curve stats for.
    """
    name = raw.get("name", "UNKNOWN")

    # Handle double-faced cards: source type_line/oracle_text/colors
    # from a single chosen FACE, never the top-level fields (see the
    # detailed comment below, after `faces = raw.get("card_faces")`,
    # for why, and for which face gets chosen -- it's the land face if
    # one exists, the front face otherwise, not always the front face).
    type_line = raw.get("type_line", "") or ""
    oracle_text = raw.get("oracle_text", "") or ""
    mana_cost = raw.get("mana_cost", "") or ""
    cmc = raw.get("cmc", 0) or 0
    colors = raw.get("colors")
    produced_mana = raw.get("produced_mana")
    art_crop_url = (raw.get("image_uris") or {}).get("art_crop", "")

    faces = raw.get("card_faces")
    if faces:
        # Scryfall does NOT reliably leave top-level fields blank for
        # double-faced cards -- some layouts (e.g. "prepare", a newer
        # mechanic) populate top-level mana_cost/type_line as a
        # "{front} // {back}" concatenation of BOTH faces, which is
        # truthy and so would silently bypass an "only fall back if
        # falsy" check. Always sourcing from a single chosen FACE's own
        # fields (never the concatenated top-level ones) avoids that
        # regardless of which layout produced the concatenation.
        #
        # Which face: if either face is a land (the "modal spell //
        # land" cycle -- Legion Leadership // Legion Stronghold, etc.,
        # and true land/land Pathway-style cards), MODEL THE CARD AS
        # THAT LAND, full stop -- ignore the spell face entirely for
        # curve-modeling purposes (DESIGN DECISION, not just a parsing
        # default: see CHANGELOG Pass 10). Otherwise, fall back to the
        # front face, as before Pass 10 (e.g. Sygg, a transform card
        # with no land face at all, still reads as its front/only-
        # castable side).
        land_face = next((f for f in faces if "Land" in (f.get("type_line") or "")), None)
        chosen = land_face if land_face is not None else faces[0]

        type_line = chosen.get("type_line", "") or type_line
        oracle_text = chosen.get("oracle_text", "") or oracle_text
        if chosen.get("colors") is not None:
            colors = chosen.get("colors")
        # Multi-faced layouts usually have NO top-level image_uris at
        # all (each face has its own separate illustration) -- fall
        # back to the chosen face's own art only when the top-level
        # lookup above came up empty, same "only override if we didn't
        # already get something real" shape as the mana_cost fallback
        # a few lines down.
        if not art_crop_url:
            art_crop_url = (chosen.get("image_uris") or {}).get("art_crop", "")

        if land_face is not None:
            # Lands have no mana cost or mana value -- force these
            # rather than inheriting a spell-face value (which, per
            # the "modal spell // land" cycle, is what the un-modeled
            # OTHER face would have had).
            mana_cost = ""
            cmc = 0
        else:
            mana_cost = chosen.get("mana_cost", "") or mana_cost

    is_land = "Land" in type_line
    is_artifact = "Artifact" in type_line
    is_creature = "Creature" in type_line
    has_tap_for_mana = bool(TAP_FOR_MANA_RE.search(PAREN_RE.sub("", oracle_text)))
    has_haste = "haste" in oracle_text.lower()

    is_mana_rock = (not is_land) and is_artifact and (not is_creature) and has_tap_for_mana
    is_mana_dork = is_creature and has_tap_for_mana
    is_ramp_spell = (not is_land) and (not is_artifact) and (not is_creature) \
        and _ramp_puts_onto_battlefield(oracle_text)

    # Work out what colors this thing can produce mana in (only
    # meaningful for lands / mana rocks / mana dorks).
    if produced_mana:
        produced_colors = frozenset(produced_mana)
    elif is_ramp_spell:
        # Land-tutor spells that fetch "a basic land card" of no
        # specified color can, in principle, fetch any color the deck
        # actually runs -- approximate with the deck's overall color
        # identity footprint.
        produced_colors = deck_colors if deck_colors else frozenset({"C"})
    else:
        # Also covers fetchlands: they have no `produced_mana` of their
        # own (their sac ability isn't a mana ability) and are excluded
        # from the is_ramp_spell branch above by `not is_land`. This is
        # correct now, not a bug -- see fetch_info below, which is how
        # a fetchland's actual mana potential gets represented.
        produced_colors = frozenset()

    mana_per_tap = _tap_ability_mana_count(oracle_text) if (is_mana_rock or is_mana_dork) else 1
    lands_fetched = _lands_fetched_count(oracle_text) if is_ramp_spell else 1
    fetched_tapped = ASSUME_RAMP_LANDS_TAPPED and not has_haste

    # Fetchland / tapped-state classification. Only meaningful for
    # lands; nonland permanents don't have this kind of battlefield-
    # entry state in scope for this tool.
    fetch_info = _parse_fetch_land(oracle_text) if is_land else None
    tapped_kind, check_types = _parse_tapped_kind(oracle_text) if is_land else ("UNTAPPED", frozenset())

    card = Card(
        name=name,
        quantity=1,  # filled in by caller from the decklist counts
        cmc=int(round(cmc)),
        mana_cost=mana_cost,
        type_line=type_line,
        oracle_text=oracle_text,
        colors=frozenset(colors or []),
        color_identity=frozenset(raw.get("color_identity", []) or []),
        art_crop_url=art_crop_url or "",
        is_land=is_land,
        is_mana_rock=is_mana_rock,
        is_mana_dork=is_mana_dork,
        is_ramp_spell=is_ramp_spell,
        mana_per_tap=mana_per_tap,
        produced_colors=produced_colors,
        lands_fetched=lands_fetched,
        fetched_lands_tapped=fetched_tapped,
        tapped_kind=tapped_kind,
        check_types=check_types,
    )
    if fetch_info:
        for field_name, value in fetch_info.items():
            setattr(card, field_name, value)
    return card


# =======================================================================
# STEP 4: MANA COST PARSING & PAYMENT
# =======================================================================
HYBRID_RE = re.compile(r"^([WUBRG])/([WUBRG])$")
HYBRID_GENERIC_RE = re.compile(r"^(\d+)/([WUBRG])$")
PHYREXIAN_RE = re.compile(r"^([WUBRG])/P$")


@dataclass
class ManaCost:
    """
    A parsed mana cost, split into the shape `try_pay_cost` needs:
    exact colored pips, an exact colorless-symbol requirement, flexible
    hybrid pips, and a generic total that any leftover mana can pay.
    """
    generic: int = 0                              # {N} symbols (+ X, + hybrid-generic; see parse_mana_cost)
    colored: dict = field(default_factory=dict)    # {"U": 2} for two {U} symbols
    colorless_pips: int = 0                        # {C} symbols -- needs a source that makes colorless specifically
    hybrid: list = field(default_factory=list)     # one frozenset({"W","U"}) per {W/U} symbol


def parse_mana_cost(mana_cost: str) -> ManaCost:
    """
    Parse a Scryfall mana cost string ("{1}{U}{U}", "{X}{R}{R}", ...)
    into a ManaCost. A few symbol kinds are deliberately simplified
    rather than modeled exactly (none of these make a card look
    *easier* to cast than it truly is -- all conservative):
      - {X}: contributes 0 to generic, matching Scryfall's own `cmc`
        convention (an X spell's cmc is computed with X=0). "On curve"
        for an X spell means castable for X=0 on its cmc turn, not
        "using X for something meaningful."
      - Phyrexian ({W/P}): treated as a hard colored pip for that
        color -- the life-payment alternative is ignored, consistent
        with this tool not tracking life totals at all (see module
        docstring, assumption 11).
      - Hybrid-generic ({2/W}): folded into `generic` at face value,
        ignoring the "or pay {W} instead" alternative. Any source that
        could pay the colored side can also pay generic, so this can
        only make a cost look harder than it is, never easier.
      - Anything unrecognized (snow {S}, etc.): folded into `generic`
        as a safe default -- this tool has no concept of snow mana.
    """
    cost = ManaCost()
    for symbol in _extract_mana_symbols(mana_cost):
        if symbol.isdigit():
            cost.generic += int(symbol)
        elif symbol == "X":
            continue
        elif symbol == "C":
            cost.colorless_pips += 1
        elif symbol in COLOR_LETTERS:
            cost.colored[symbol] = cost.colored.get(symbol, 0) + 1
        elif HYBRID_RE.match(symbol):
            m = HYBRID_RE.match(symbol)
            cost.hybrid.append(frozenset({m.group(1), m.group(2)}))
        elif HYBRID_GENERIC_RE.match(symbol):
            cost.generic += int(HYBRID_GENERIC_RE.match(symbol).group(1))
        elif PHYREXIAN_RE.match(symbol):
            color = PHYREXIAN_RE.match(symbol).group(1)
            cost.colored[color] = cost.colored.get(color, 0) + 1
        else:
            cost.generic += 1
    return cost


def _mana_pool_from_sources(sources) -> list:
    """
    Expand ready ManaSources into individual one-mana units (a Sol
    Ring contributes 2 units, both colorless {"C"}). Each unit is
    represented as the frozenset of colors it can pay -- a unit from a
    Command Tower might be frozenset({"W","U","B","R","G"}).
    """
    pool = []
    for source in sources:
        if source.ready:
            pool.extend([source.colors] * source.amount)
    return pool


def try_pay_cost(cost: ManaCost, pool: list):
    """
    Attempt to pay `cost` out of `pool` (a list of per-unit color
    frozensets). Returns the resulting pool with spent units removed
    on success, or None if unpayable -- `pool` itself is never
    mutated, so a failed/rejected attempt costs the caller nothing.

    Greedy "most-constrained-requirement-first, least-flexible-unit-
    first" matcher (module docstring assumption 5): {C} and exact
    colored pips are locked in before flexible hybrid pips, and each
    pip is paid with whichever matching unit has the FEWEST other uses,
    so flexible sources (Command Tower, a 5-color rock) are preserved
    for whichever requirement needs them most. This is a good
    approximation, not an exhaustive constraint solver -- a
    sufficiently adversarial mana base could need backtracking this
    doesn't do (see module docstring assumption 5).
    """
    remaining = list(pool)

    requirements = []
    if cost.colorless_pips:
        requirements.append((frozenset({"C"}), cost.colorless_pips))
    for color, count in cost.colored.items():
        requirements.append((frozenset({color}), count))
    for allowed in cost.hybrid:
        requirements.append((allowed, 1))
    requirements.sort(key=lambda req: len(req[0]))

    for allowed, count in requirements:
        for _ in range(count):
            candidates = [i for i, unit in enumerate(remaining) if unit & allowed]
            if not candidates:
                return None
            best = min(candidates, key=lambda i: len(remaining[i]))
            del remaining[best]

    if len(remaining) < cost.generic:
        return None
    remaining = sorted(remaining, key=len)
    del remaining[:cost.generic]
    return remaining


def can_pay_cost(cost: ManaCost, sources) -> bool:
    """
    True if `cost` is payable from the ready mana in `sources` (a list
    of ManaSource). This is the "does it exist at all" check; for
    sequential deployment within a turn (spend mana, see what's left
    for the next thing), build a pool with `_mana_pool_from_sources`
    once and call `try_pay_cost` directly so payments actually persist.
    """
    return try_pay_cost(cost, _mana_pool_from_sources(sources)) is not None


# =======================================================================
# STEP 5: LAND DROPS -- fetchland and enters-tapped resolution
# =======================================================================
def _battlefield_lands(battlefield: list) -> list:
    """ManaSources on `battlefield` whose originating Card is a land.
    Ramp-spell-created lands have `card=None` (see deploy_accelerant --
    a ramp spell's actual fetched land isn't modeled as a real Card,
    just an approximate colors/tapped ManaSource) and so are NOT
    counted here. This means a SLOW/FAST/BATTLE land drawn later in a
    ramp-heavy game may be misjudged, since ramped-in lands don't
    register as "other lands" for its condition. Known limitation,
    flagged rather than silently accepted."""
    return [ms for ms in battlefield if ms.card is not None and ms.card.is_land]


def resolve_tapped_state(card: Card, battlefield: list) -> bool:
    """
    True if `card`, entering play as a land right now, comes in ready
    (untapped); False if it enters tapped. Evaluates `card.tapped_kind`
    against the CURRENT `battlefield` (i.e. lands already in play,
    which is exactly "other lands" for the SLOW/FAST/BATTLE cycles'
    own wording, since this card isn't in `battlefield` yet).

    EDH-specific resolution: BOND lands ("...unless you have two or
    more opponents") always resolve ready -- this tool models one
    deck's curve in a Commander pod, where >= 2 opponents is the
    default assumption rather than something separately simulated.
    """
    kind = card.tapped_kind
    if kind in ("UNTAPPED", "BOND"):
        return True
    if kind == "ALWAYS":
        return False

    other_lands = _battlefield_lands(battlefield)
    if kind == "SLOW":
        return len(other_lands) >= 2
    if kind == "FAST":
        return len(other_lands) <= 2
    if kind == "BATTLE":
        basics = sum(1 for ms in other_lands if "Basic" in ms.card.type_line)
        return basics >= 2
    if kind == "CHECK":
        return any(
            any(t in ms.card.type_line for t in card.check_types)
            for ms in other_lands
        )
    return True  # unrecognized kind -- shouldn't happen, fail open (optimistic)


def resolve_fetch(fetch_card: Card, library: list, preferred_colors: frozenset) -> Optional[Card]:
    """
    Crack `fetch_card` for one legal target, removing and returning it
    from `library` (real deck-thinning, not a peek) -- or None on a
    whiff (no legal target left; the fetchland is still sacrificed for
    nothing, same as in a real game).

    Modeling decision: a fetch is always cracked the SAME turn it's
    played. There's no in-game reason for a curve simulator to model
    holding one uncracked -- delaying a crack is a real-game tactic
    for hiding information or dodging removal, neither of which this
    tool models, so cracking immediately is strictly the better
    curve-out play and the only one worth simulating.

    Known limitation: a fetch will never target another fetchland,
    even if one would otherwise be a legal target (e.g. Prismatic
    Vista finding, and immediately also cracking, Wooded Foothills).
    This avoids modeling fetch-into-fetch chains; it's rare enough in
    real decks that the lost precision should be negligible.
    """
    def is_legal(card: Card) -> bool:
        if not card.is_land or card.is_fetch_land:
            return False
        if fetch_card.fetch_kind == "BASIC":
            return "Basic" in card.type_line
        if fetch_card.fetch_kind == "TYPED":
            return any(t in card.type_line for t in fetch_card.fetch_subtypes)
        return True  # "ANY"

    candidates = [c for c in library if is_legal(c)]
    if not candidates:
        return None

    # Prefer a candidate that produces a currently-needed color, same
    # heuristic as an ordinary land drop (assumption 6).
    if preferred_colors:
        narrowed = [c for c in candidates if c.produced_colors & preferred_colors]
        if narrowed:
            candidates = narrowed

    chosen = candidates[0]
    library.remove(chosen)
    return chosen


def _colors_needed_by_cheapest_uncastable(hand: list, pool: list) -> frozenset:
    """
    The color(s) required by the cheapest not-yet-castable NONLAND
    card(s) in `hand` -- mana rocks, mana dorks, and ramp spells
    compete in this same search on equal footing with ordinary action
    spells (Pass 15), not a separate priority tier: whichever nonland
    card is cheapest by cmc wins, regardless of category. Drives both
    land-drop and fetch-target selection (assumption 6: chase the
    colors the next thing you actually want to cast needs, not color
    diversity in general).

    Before Pass 15, this only looked at `is_action_spell` cards,
    meaning a mana rock's or dork's own color requirement never
    factored into which land got played -- even though
    `deploy_accelerant` tries to cast the cheapest affordable
    accelerant every turn regardless. A hand holding an uncastable
    1-mana dork and an off-color action spell would chase only the
    spell's color, leaving the land drop unable to help set up the
    accelerant `deploy_accelerant` was about to try casting anyway.
    Fixed by broadening the candidate pool to every nonland card; a
    cheap dork/rock naturally wins this search when it's genuinely the
    cheapest not-yet-castable thing in hand (common, since 1-mana
    accelerants are common), without letting a PRICIER accelerant
    preempt a cheaper action spell's need -- cmc still decides, not
    category.

    When multiple not-yet-castable cards TIE for cheapest, this
    returns the UNION of all their colors, not just one arbitrarily
    picked card's -- true regardless of which categories the tied
    cards belong to. Only tracking a single card's colors here was a
    measured, real limitation: whichever card happened to be first in
    hand-iteration order (an accident of draw order, not a deliberate
    priority) would win the land drop every time, silently starving
    same-cost siblings of a matching land even when one was in hand.
    Concretely: for a one-off {W} action spell in a hand that also held
    any other not-yet-castable cmc-1 card, this cost it a matching
    white land roughly 7% of the time it was drawn, in testing against
    a real 3-color decklist -- a land that could have made both colors
    (e.g. an R/W dual matching a white AND a red 1-drop) was being
    ignored in favor of whichever card won the coin-flip (Pass 6).
    Aggregating the tied tier lets `play_land_drop`'s scoring recognize
    a land that helps ANY of them, including one that helps all of them.
    """
    candidates = sorted((c for c in hand if not c.is_land), key=lambda c: c.cmc)
    needed = set()
    cheapest_cmc = None
    for card in candidates:
        if cheapest_cmc is not None and card.cmc > cheapest_cmc:
            break
        cost = parse_mana_cost(card.mana_cost)
        if try_pay_cost(cost, pool) is None:
            if cheapest_cmc is None:
                cheapest_cmc = card.cmc
            needed |= set(cost.colored.keys())
            for pair in cost.hybrid:
                needed |= pair
    return frozenset(needed)


def play_land_drop(hand: list, library: list, battlefield: list) -> Optional[Card]:
    """
    Choose and play one land from `hand`, mutating hand/library/
    battlefield in place. Returns the Card actually played -- for a
    fetchland this is the FETCHED land, not the fetchland itself (the
    fetchland is sacrificed and never sits on the battlefield as its
    own permanent in this model; see resolve_fetch). Returns None if
    there's no land in hand to play.

    Selection heuristic (assumption 6), highest priority first:
      1. hits a color the cheapest not-yet-castable nonland card in
         hand needs (mana rock/dork/ramp spell or action spell alike),
      2. enters untapped this turn (fetches score on fetch_forces_tapped
         instead, since their true tapped state depends on what they find),
      3. is a fetchland (free deck-thinning), as a last tiebreak.
    """
    lands_in_hand = [c for c in hand if c.is_land]
    if not lands_in_hand:
        return None

    pool = _mana_pool_from_sources(battlefield)
    needed_colors = _colors_needed_by_cheapest_uncastable(hand, pool)

    def land_score(card: Card) -> tuple:
        hits_need = bool(needed_colors and card.produced_colors & needed_colors)
        if card.is_fetch_land:
            fetch_hits_need = bool(needed_colors and card.fetch_subtypes & needed_colors) \
                if card.fetch_kind == "TYPED" else bool(needed_colors)
            return (fetch_hits_need, not card.fetch_forces_tapped, True)
        return (hits_need, resolve_tapped_state(card, battlefield), False)

    lands_in_hand.sort(key=land_score, reverse=True)
    chosen = lands_in_hand[0]
    hand.remove(chosen)

    if chosen.is_fetch_land:
        target = resolve_fetch(chosen, library, needed_colors)
        if target is None:
            return chosen  # whiffed -- sacrificed for nothing, no ManaSource added
        ready = (not chosen.fetch_forces_tapped) and resolve_tapped_state(target, battlefield)
        battlefield.append(ManaSource(colors=target.produced_colors, amount=1, ready=ready, card=target))
        return target

    ready = resolve_tapped_state(chosen, battlefield)
    battlefield.append(ManaSource(colors=chosen.produced_colors, amount=chosen.mana_per_tap, ready=ready, card=chosen))
    return chosen


# =======================================================================
# STEP 6: TURN ENGINE -- accelerants, curve checks, mulligans, one game
# =======================================================================
def deploy_accelerant(hand: list, battlefield: list) -> tuple:
    """
    Deploy at most one mana rock, mana dork, or ramp spell this turn --
    the cheapest one currently affordable (assumption 4: a player plays
    a land and one accelerant per turn, sequenced by cost). Mutates
    hand/battlefield in place. Returns (card_played_or_None, pool)
    where `pool` is this turn's mana pool AFTER paying for it (the
    pool `_mana_pool_from_sources(battlefield)` would already reflect,
    handed back so the caller doesn't have to rebuild it).
    """
    pool = _mana_pool_from_sources(battlefield)
    candidates = sorted(
        (c for c in hand if c.is_mana_rock or c.is_mana_dork or c.is_ramp_spell),
        key=lambda c: c.cmc,
    )

    for card in candidates:
        new_pool = try_pay_cost(parse_mana_cost(card.mana_cost), pool)
        if new_pool is None:
            continue
        hand.remove(card)

        if card.is_mana_rock:
            # Usable the same turn -- summoning sickness only restricts
            # creatures (assumption 1), not artifacts.
            battlefield.append(ManaSource(colors=card.produced_colors, amount=card.mana_per_tap,
                                           ready=True, card=card))
            new_pool = new_pool + [card.produced_colors] * card.mana_per_tap
        elif card.is_mana_dork:
            # Usable starting NEXT turn (assumption 2: creatures have
            # summoning sickness) unless the card has Haste. Re-checked
            # from oracle_text here rather than a stored field, since
            # Haste only matters for this one branch.
            has_haste = "haste" in card.oracle_text.lower()
            battlefield.append(ManaSource(colors=card.produced_colors, amount=card.mana_per_tap,
                                           ready=has_haste, card=card))
            if has_haste:
                new_pool = new_pool + [card.produced_colors] * card.mana_per_tap
        else:
            # Ramp spell: puts `lands_fetched` lands into play, ready
            # next turn unless untapped (assumption 3). These lands
            # have no backing Card (the spell doesn't specify which
            # real land it finds) -- see _battlefield_lands for the
            # SLOW/FAST/BATTLE-condition consequence of that.
            for _ in range(card.lands_fetched):
                land_ready = not card.fetched_lands_tapped
                battlefield.append(ManaSource(colors=card.produced_colors, amount=1,
                                               ready=land_ready, card=None))
                if land_ready:
                    new_pool = new_pool + [card.produced_colors]

        return card, new_pool

    return None, pool


# How many turns past a card's own cmc to keep checking mana
# availability for (Pass 7). For a cmc-1 card this reports turns
# 1, 2, 3, 4.
MANA_AVAILABILITY_LOOKAHEAD = 3


def record_mana_availability(card_lookup: dict, parsed_costs: dict, pool: list,
                              turn: int, results: dict) -> None:
    """
    For every NONLAND card in `card_lookup` whose curve window
    [cmc, cmc + MANA_AVAILABILITY_LOOKAHEAD] includes `turn`, check
    whether `pool` (this turn's leftover mana, after the land drop and
    the turn's single accelerant) could pay its cost -- independent of
    whether that card is actually in this game's hand.

    This is deliberately decoupled from the draw lottery (Pass 7):
    earlier versions of this tool asked "was this specific card drawn
    AND payable," which meant a card's number was capped by its own
    draw probability (~8% for a 1-of in a ~100-card library) no matter
    how good the manabase was for it -- conflating "will I draw this"
    with "can my mana base support this," when a deckbuilder asking
    about a card's colored-mana requirements wants the latter alone.
    `results` accumulates `results[name][turn] = bool` (keyed by the
    same `name` string used for `card_lookup`/`parsed_costs`, not by
    reading `card.name` off the Card object) across
    every nonland card and every turn in its window, checked against
    the SAME pool a hand-driven card would have seen that turn (i.e.
    real land sequencing and real accelerant deployment for whatever
    was ACTUALLY drawn that game still shapes `pool` -- only the
    "was the checked card itself in hand" gate is removed).
    """
    for name, cost in parsed_costs.items():
        card = card_lookup[name]
        start_turn = max(1, card.cmc)
        if not (start_turn <= turn <= start_turn + MANA_AVAILABILITY_LOOKAHEAD):
            continue
        results.setdefault(name, {})[turn] = try_pay_cost(cost, pool) is not None


def evaluate_hand_keepable(hand: list, library_remainder: list) -> bool:
    """
    London mulligan keep/ship decision: using ONLY the cards in `hand`
    (no further draws), can the player cast/deploy at least
    MIN_SPELLS_TO_KEEP distinct nonland cards within
    MULLIGAN_LOOKAHEAD_TURNS turns -- a deployed mana rock, mana dork,
    or ramp spell counts toward this exactly the same as a cast action
    spell (Pass 17; see below). Spells/accelerants are actually
    deployed (mana spent, card removed from the trial hand) as they
    become affordable, so two of them can't both "count" off mana that
    could only pay for one.

    Before Pass 17, deploying the turn's one accelerant (via
    `deploy_accelerant`, see the next paragraph) never counted toward
    `spells_cast` at all -- its return value was silently discarded --
    while `play_land_drop`'s own color-need search WAS already
    broadened to treat every nonland card equally back in Pass 15. A
    hand that could easily deploy two castable mana rocks over four
    turns (but held zero action spells) was judged unkeepable purely
    because of this gap, not because the hand was actually bad.

    Each simulated turn deploys AT MOST one accelerant (mirroring
    `run_single_game`'s own turn loop and assumption 4 -- a player
    plays one land and one accelerant per turn), so this only adds one
    counting fix, not a second "cast more accelerants" pass: the
    action-spell loop below is deliberately UNCHANGED (still scoped to
    `is_action_spell`) so a hand can't have both an accelerant AND an
    extra rock/dork counted in the same turn, which would let the
    trial deploy two accelerants in one turn when the real engine never
    allows more than one.

    Operates on COPIES of hand/library (`trial_hand`/`trial_library`)
    so nothing here persists into the real game -- but fetch/ramp
    resolution during the trial is otherwise the real logic (real
    searches against `trial_library`), not an approximation, since
    that's cheap here and keeps this consistent with actual play.
    """
    trial_hand = list(hand)
    trial_library = list(library_remainder)
    trial_battlefield = []
    spells_cast = 0

    for _turn in range(1, MULLIGAN_LOOKAHEAD_TURNS + 1):
        play_land_drop(trial_hand, trial_library, trial_battlefield)
        accelerant, pool = deploy_accelerant(trial_hand, trial_battlefield)
        if accelerant is not None:
            spells_cast += 1

        for card in sorted((c for c in trial_hand if c.is_action_spell), key=lambda c: c.cmc):
            new_pool = try_pay_cost(parse_mana_cost(card.mana_cost), pool)
            if new_pool is not None:
                pool = new_pool
                trial_hand.remove(card)
                spells_cast += 1

        for source in trial_battlefield:
            source.ready = True  # untap step / summoning sickness wears off

    return spells_cast >= MIN_SPELLS_TO_KEEP


def choose_cards_to_bottom(hand: list, n: int) -> list:
    """
    Adaptive London-mulligan bottoming: choose which `n` cards hurt
    the hand's plan least, rather than a fixed rule.
      - Land-heavy hand (lands are more than half of it, and there are
        at least 3): bottom excess lands first, then highest-CMC
        spells if still short of `n`.
      - Otherwise (spell-heavy or land-light): protect every land --
        a land-light hand needs all of them -- and bottom the
        highest-CMC action spells first, since those are the least
        likely to be castable soon anyway.
    Works off `hand` INDICES throughout, not card equality, since
    basic lands are value-equal to each other (dataclass default
    __eq__) -- comparing by value would treat "bottom one Forest" as
    "bottom every Forest in hand."
    """
    land_idxs = [i for i, c in enumerate(hand) if c.is_land]
    spell_idxs_desc = sorted((i for i, c in enumerate(hand) if not c.is_land),
                              key=lambda i: hand[i].cmc, reverse=True)

    bottom_idxs = []
    if len(land_idxs) > len(hand) / 2 and len(land_idxs) >= 3:
        keep_lands = max(len(hand) - n, 3)
        excess = max(len(land_idxs) - keep_lands, 0)
        bottom_idxs.extend(land_idxs[:excess])

    for i in spell_idxs_desc:
        if len(bottom_idxs) >= n:
            break
        if i not in bottom_idxs:
            bottom_idxs.append(i)

    for i in land_idxs:
        if len(bottom_idxs) >= n:
            break
        if i not in bottom_idxs:
            bottom_idxs.append(i)

    return [hand[i] for i in bottom_idxs[:n]]


def _draw(library: list, n: int) -> list:
    drawn = library[:n]
    del library[:n]
    return drawn


def draw_opening_hand(library: list) -> tuple:
    """
    Full London mulligan procedure against `library` (mutated: ends up
    as whatever's left after the kept hand and its bottomed cards are
    removed). Returns (final_hand, mulligans_taken).
    """
    mulligans = 0
    while True:
        hand = _draw(library, OPENING_HAND_SIZE)
        keepable = evaluate_hand_keepable(hand, library)
        if keepable or mulligans >= MAX_MULLIGANS:
            to_bottom = choose_cards_to_bottom(hand, mulligans)
            for card in to_bottom:
                hand.remove(card)
            library.extend(to_bottom)
            return hand, mulligans
        # Ship it: hand goes back, library reshuffles for another try.
        library.extend(hand)
        random.shuffle(library)
        mulligans += 1


def run_single_game(card_lookup: dict, library_counts: dict, parsed_costs: dict,
                     max_turns: int) -> tuple:
    """
    Simulate one full game: mulligan to a kept hand, then play out
    `max_turns` turns (draw, land drop, one accelerant, mana-
    availability checks). Returns `(results, land_counts)`:

    - `results` is `{card_name: {turn: bool}}` for every NONLAND card
      and every turn in its curve window (see record_mana_availability)
      -- whether that specific mana pool that turn could pay the
      card's cost, independent of whether the card was actually drawn
      this game.
    - `land_counts` is `{turn: (lands_in_play, lands_in_hand)}` for
      every simulated turn -- `lands_in_play` from `_battlefield_lands`
      (real Card lands only, same definition the SLOW/FAST/BATTLE
      checks already use), `lands_in_hand` counting lands drawn but
      not yet played that turn. This feeds the screw/flood detection
      in `run_simulation`; it's tracked here rather than recomputed
      later because the battlefield/hand state at each turn only
      exists transiently during this loop.

    Modeling decision: every turn draws a card, INCLUDING turn 1. This
    matches the real tournament rule for Commander specifically -- the
    "starting player skips their first draw" rule is a two-player-game
    rule; in multiplayer (the default assumption for an EDH simulator)
    the starting player does NOT skip it.
    """
    library = []
    for name, qty in library_counts.items():
        library.extend([card_lookup[name]] * qty)
    random.shuffle(library)

    hand, _mulligans = draw_opening_hand(library)
    battlefield = []
    results = {}
    land_counts = {}

    for turn in range(1, max_turns + 1):
        if library:
            hand.append(library.pop(0))

        play_land_drop(hand, library, battlefield)
        _, pool = deploy_accelerant(hand, battlefield)
        record_mana_availability(card_lookup, parsed_costs, pool, turn, results)
        land_counts[turn] = (len(_battlefield_lands(battlefield)),
                              sum(1 for c in hand if c.is_land))

        for source in battlefield:
            source.ready = True

    return results, land_counts


def run_simulation(card_lookup: dict, library_counts: dict, num_simulations: int,
                    max_turns: int = MAX_SIMULATED_TURNS,
                    progress_callback: Optional[Callable[[int, int], None]] = None) -> tuple:
    """
    Run `num_simulations` independent games and aggregate, per NONLAND
    card in the decklist (mana rocks, dorks, ramp spells, and action
    spells alike -- everything that isn't a land) and per turn in its
    curve window [cmc, cmc + MANA_AVAILABILITY_LOOKAHEAD], the fraction
    of games in which the mana existed that turn to pay its cost.
    Also aggregates two deck-wide mana-CONSISTENCY stats (screw/flood
    probability, see the SCREW_*/FLOOD_* constants) that don't belong
    to any one card.

    This is a mana-base question, not a "will I draw this" question
    (Pass 7): the denominator is `num_simulations`, but unlike the
    pre-Pass-7 metric this no longer requires the card to have been
    drawn that game -- every game's actual land/accelerant sequencing
    (driven by whatever WAS drawn) produces a pool each turn, and every
    nonland card's cost is checked against that pool regardless of
    whether the card itself showed up in hand. "Could my mana base pay
    for a {1}{U}{U} spell by turn 4" no longer gets capped at ~8% by
    the odds of drawing one specific copy of it.

    Returns `(card_results, mana_consistency)`:
    - `card_results` is `{card_name: {turn: probability}}`. A card
      whose cmc exceeds `max_turns` has an empty inner dict (its
      window never overlaps a simulated turn) -- the caller should
      report that distinctly rather than reading it as "0% available."
    - `mana_consistency` is `{"screw_prob": float|None,
      "flood_prob": float|None}` -- the fraction of games that hit the
      SCREW_CHECK_TURN/FLOOD_CHECK_TURN thresholds. Either is None
      if `max_turns` is too short to reach the relevant checkpoint
      turn at all (same "don't report a number you didn't actually
      measure" principle as the per-card cmc-exceeds-max_turns case).

    `progress_callback`, if given, is called periodically as
    `progress_callback(games_done, num_simulations)` -- roughly 200
    times over the whole run (see REPORT_EVERY below), not every
    single game, so the callback overhead never becomes a meaningful
    fraction of simulation time even for very large `num_simulations`.
    Used by the web app to drive a live "N / total games" progress
    bar; None (the default) costs nothing extra and is what the CLI
    uses.
    """
    eligible_names = [
        name for name, card in card_lookup.items()
        if not card.is_land and name in library_counts
    ]
    parsed_costs = {name: parse_mana_cost(card_lookup[name].mana_cost) for name in eligible_names}
    turn_counts = defaultdict(lambda: defaultdict(int))
    screw_count = 0
    flood_count = 0
    report_every = max(1, num_simulations // 200)

    for i in range(num_simulations):
        results, land_counts = run_single_game(card_lookup, library_counts, parsed_costs, max_turns)
        for name, per_turn in results.items():
            for turn, ok in per_turn.items():
                if ok:
                    turn_counts[name][turn] += 1
        if SCREW_CHECK_TURN in land_counts:
            lands_in_play, _ = land_counts[SCREW_CHECK_TURN]
            if lands_in_play <= SCREW_LAND_THRESHOLD:
                screw_count += 1
        if FLOOD_CHECK_TURN in land_counts:
            _, lands_in_hand = land_counts[FLOOD_CHECK_TURN]
            if lands_in_hand >= FLOOD_EXCESS_LAND_THRESHOLD:
                flood_count += 1
        if progress_callback is not None and ((i + 1) % report_every == 0 or i + 1 == num_simulations):
            progress_callback(i + 1, num_simulations)

    card_results = {
        name: {turn: turn_counts[name][turn] / num_simulations
               for turn in range(max(1, card_lookup[name].cmc),
                                  max(1, card_lookup[name].cmc) + MANA_AVAILABILITY_LOOKAHEAD + 1)
               if turn <= max_turns}
        for name in eligible_names
    }
    mana_consistency = {
        "screw_prob": (screw_count / num_simulations) if SCREW_CHECK_TURN <= max_turns else None,
        "flood_prob": (flood_count / num_simulations) if FLOOD_CHECK_TURN <= max_turns else None,
    }
    return card_results, mana_consistency


# =======================================================================
# STEP 7: CLI
# =======================================================================
def _build_card_lookup(names: set, cache_path: Path, verbose: bool = False,
                        log_fn: Optional[Callable[[str], None]] = None) -> dict:
    """
    Fetch + classify every card in `names` (decklist spelling -- e.g.
    "Sol Ring" as typed in the deck file, NOT necessarily Scryfall's
    canonical capitalization). Returns {decklist_name: Card}, skipping
    (and warning about) any name Scryfall couldn't resolve. Caches to
    `cache_path`, saved once at the end regardless of how many cards
    were newly fetched.

    `log_fn`, if given, receives a live line for each card as it's
    looked up (see `_emit`) -- used by the web app to stream fetch
    progress instead of printing to the process-wide stdout/stderr.
    """
    cache = load_cache(cache_path)
    raws = {}
    for name in sorted(names):
        raw = fetch_card_data(name, cache, verbose=verbose, log_fn=log_fn)
        if raw is not None:
            raws[name] = raw
    save_cache(cache_path, cache)

    missing = sorted(names - raws.keys())
    if missing:
        _emit(f"Warning: {len(missing)} card(s) not found on Scryfall, excluded from "
              f"the simulation: {', '.join(missing)}", log_fn, file=sys.stderr)

    # Deck color identity, used by classify_card to approximate what
    # color a generic "search for a basic land" ramp spell can fetch.
    deck_colors = frozenset().union(
        *[frozenset(raw.get("color_identity", []) or []) for raw in raws.values()]
    ) if raws else frozenset()

    card_lookup = {name: classify_card(raw, deck_colors) for name, raw in raws.items()}

    # Force card.name to match the card_lookup KEY (the decklist
    # spelling), not whatever classify_card set it to from raw["name"]
    # (Scryfall's own canonical name). These two are usually identical
    # but can genuinely differ -- most importantly for MDFCs, where
    # Scryfall's top-level name is "Front // Back" while the decklist
    # only ever names one face.
    #
    # This was load-bearing, not just defensive, against the
    # pre-Pass-7 turn engine: `check_on_curve_castability` (since
    # replaced) recorded results keyed by `card.name` while aggregation
    # read `card_lookup`'s own keys, so a divergence here caused a
    # real, silent bug -- "Sygg, Wanderwine Wisdom" (decklist) vs.
    # "Sygg, Wanderwine Wisdom // Sygg, Wanderbrine Shield" (Scryfall's
    # canonical name for that MDFC) produced a permanent 0% (Pass 5).
    # `record_mana_availability`, its Pass 7 replacement, keys entirely
    # off the `name` string (`parsed_costs`/`card_lookup`'s own keys)
    # and never reads `card.name` at all, so this line no longer
    # prevents an active bug -- it's kept so `card.name` stays a
    # trustworthy identity field for any other code (current or
    # future) that reasonably expects it to match how the card is
    # addressed everywhere else (dict keys, console/CSV output).
    for name, card in card_lookup.items():
        card.name = name

    return card_lookup


# =======================================================================
# STEP 8: DECKBUILDER-FACING STATISTICS SUMMARY (--summary)
# =======================================================================
_SUMMARY_BAR_CHAR = "#"
_SUMMARY_BAR_WIDTH = 36
_SUMMARY_COLOR_ORDER = ["W", "U", "B", "R", "G", "C"]
_SUMMARY_COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}


def _summary_bar(value, max_value, width=_SUMMARY_BAR_WIDTH):
    if max_value <= 0:
        return ""
    return _SUMMARY_BAR_CHAR * round((value / max_value) * width)


def _summary_pct(x):
    return f"{x * 100:.1f}%"


def _summary_mana_base(library_counts: dict, card_lookup: dict) -> dict:
    """Land counts, per-color source counts (lands and rocks/dorks
    tracked separately), and a few land-quality tallies (fetches,
    enters-tapped) -- the raw numbers behind the summary's section 1."""
    land_names = [n for n in library_counts if card_lookup[n].is_land]
    total_lands = sum(library_counts[n] for n in land_names)

    land_color_copies = defaultdict(int)
    for n in land_names:
        for col in card_lookup[n].produced_colors:
            land_color_copies[col] += library_counts[n]

    rock_dork_color_copies = defaultdict(int)
    rock_count = dork_count = ramp_count = 0
    for n in library_counts:
        c = card_lookup[n]
        if c.is_mana_rock:
            rock_count += library_counts[n]
            for col in c.produced_colors:
                rock_dork_color_copies[col] += library_counts[n]
        elif c.is_mana_dork:
            dork_count += library_counts[n]
            for col in c.produced_colors:
                rock_dork_color_copies[col] += library_counts[n]
        elif c.is_ramp_spell:
            ramp_count += library_counts[n]

    fetch_count = sum(library_counts[n] for n in land_names if card_lookup[n].is_fetch_land)
    tapped_count = sum(
        library_counts[n] for n in land_names
        if card_lookup[n].tapped_kind not in ("UNTAPPED", "BOND")
    )
    return {
        "total_lands": total_lands, "land_color_copies": land_color_copies,
        "rock_dork_color_copies": rock_dork_color_copies, "rock_count": rock_count,
        "dork_count": dork_count, "ramp_count": ramp_count,
        "fetch_count": fetch_count, "tapped_count": tapped_count,
    }


def _summary_curve_histogram(card_lookup: dict, library_counts: dict) -> dict:
    hist = defaultdict(int)
    for n in library_counts:
        if not card_lookup[n].is_land:
            hist[card_lookup[n].cmc] += library_counts[n]
    return hist


def _summary_color_reliability(card_lookup: dict, results: dict) -> dict:
    """Average turn-of-CMC availability among single-colored-pip cards,
    grouped by color -- isolates "does this color show up reliably"
    from pip-count difficulty (a {W}{W} or {W/U} card doesn't count)."""
    by_color = defaultdict(list)
    for name, card in card_lookup.items():
        if card.is_land or name not in results:
            continue
        cost = parse_mana_cost(card.mana_cost)
        if len(cost.colored) == 1 and not cost.hybrid:
            (color, count), = cost.colored.items()
            if count == 1:
                on_curve = results[name].get(card.cmc)
                if on_curve is not None:
                    by_color[color].append(on_curve)
    return {color: sum(vals) / len(vals) for color, vals in by_color.items() if vals}


def _summary_deck_average(library_counts: dict, results: dict, card_lookup: dict) -> dict:
    """
    Quantity-weighted average, across every nonland card in the deck,
    of each turn offset's mana-availability probability -- "if you
    reached into the deck and pulled one nonland card out at random,
    what's the average chance its cost was payable that turn." Offset
    0 is "on curve" (turn == cmc): the headline heuristic for
    comparing two different decks/manabases at a glance. A card whose
    cmc + offset exceeds --max-turns is excluded from that offset's
    average rather than counted as 0%.
    """
    sums = defaultdict(float)
    weights = defaultdict(int)
    for name, per_turn in results.items():
        card = card_lookup.get(name)
        if card is None:
            continue
        qty = library_counts.get(name, 1)
        for offset in range(MANA_AVAILABILITY_LOOKAHEAD + 1):
            turn = card.cmc + offset
            if turn in per_turn:
                sums[offset] += per_turn[turn] * qty
                weights[offset] += qty
    return {offset: (sums[offset] / weights[offset] if weights[offset] else None)
            for offset in range(MANA_AVAILABILITY_LOOKAHEAD + 1)}


def _summary_deck_stddev(library_counts: dict, results: dict, card_lookup: dict,
                          deck_avg: dict) -> dict:
    """
    Quantity-weighted standard deviation, across every nonland card in
    the deck, of each turn offset's mana-availability probability --
    paired with `_summary_deck_average`'s mean, this answers "how much
    does that headline percentage actually represent the whole deck."
    A low stddev means every nonland card is roughly equally reliable;
    a high one means the mean is being pulled around by a mix of very
    safe and very shaky cards (e.g. a pile of colorless rocks propping
    up an average that most colored spells don't actually meet) --
    worth knowing before trusting the average alone. Uses the SAME
    weighting (card copies) and the SAME per-offset population (cards
    whose curve window covers that offset) as `_summary_deck_average`,
    so the two numbers describe the same underlying set of values.
    """
    sums_sq = defaultdict(float)
    weights = defaultdict(int)
    for name, per_turn in results.items():
        card = card_lookup.get(name)
        if card is None:
            continue
        qty = library_counts.get(name, 1)
        for offset in range(MANA_AVAILABILITY_LOOKAHEAD + 1):
            turn = card.cmc + offset
            mean = deck_avg.get(offset)
            if turn in per_turn and mean is not None:
                sums_sq[offset] += qty * (per_turn[turn] - mean) ** 2
                weights[offset] += qty
    return {offset: (math.sqrt(sums_sq[offset] / weights[offset]) if weights[offset] else None)
            for offset in range(MANA_AVAILABILITY_LOOKAHEAD + 1)}


def _summary_card_table(card_lookup: dict, results: dict) -> str:
    rows = []
    for name, per_turn in results.items():
        card = card_lookup.get(name)
        if card is None:
            continue
        cmc = card.cmc
        cells = [per_turn.get(cmc + o) for o in range(MANA_AVAILABILITY_LOOKAHEAD + 1)]
        rows.append((cmc, name, card.mana_cost or "-", cells))
    rows.sort(key=lambda r: (r[0], r[1]))

    lines = []
    header = f"{'CMC':>3}  {'Card':<38} {'Cost':<12} {'On-Curve':>9} {'+1':>8} {'+2':>8} {'+3':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for cmc, name, cost, cells in rows:
        cell_strs = [_summary_pct(c) if c is not None else "  --  " for c in cells]
        display_name = name if len(name) <= 38 else name[:35] + "..."
        lines.append(
            f"{cmc:>3}  {display_name:<38} {cost:<12} "
            f"{cell_strs[0]:>9} {cell_strs[1]:>8} {cell_strs[2]:>8} {cell_strs[3]:>8}"
        )
    return "\n".join(lines)


def _summary_top_n(results: dict, card_lookup: dict, n: int, key_fn, reverse=True, filter_fn=None):
    items = []
    for name, per_turn in results.items():
        card = card_lookup.get(name)
        if card is None:
            continue
        if filter_fn and not filter_fn(name, card, per_turn):
            continue
        v = key_fn(name, card, per_turn)
        if v is not None:
            items.append((v, name, card, per_turn))
    items.sort(key=lambda x: x[0], reverse=reverse)
    return items[:n]


def _summary_takeaways(mana: dict, curve_hist: dict, color_avgs: dict, results: dict,
                        card_lookup: dict, total_cards: int, nonland_count: int, max_turns: int) -> list:
    """Data-driven bullet points: land density, color balance, curve
    shape, land quality, and consistency drains. Every claim here is
    computed from the same numbers already shown above it, not a
    separate heuristic -- this section just narrates them."""
    out = []

    land_pct = mana["total_lands"] / total_cards if total_cards else 0
    source_count = mana["total_lands"] + mana["rock_count"] + mana["dork_count"]
    source_pct = source_count / total_cards if total_cards else 0
    if land_pct < 0.34:
        out.append(f"* LAND COUNT: {mana['total_lands']} lands ({land_pct:.0%} of the deck) is light for "
                    f"Commander -- the usual guideline is ~36-38 (37-38%). Counting the "
                    f"{mana['rock_count']} mana rocks and {mana['dork_count']} dorks as pseudo-lands "
                    f"brings effective sources to {source_count} ({source_pct:.0%}), which reads "
                    f"{'comfortably' if source_pct >= 0.38 else 'still a bit thin'} -- "
                    f"{'the rock/dork package is doing real work here.' if source_pct >= 0.38 else 'consider one or two more lands or cheap fixing.'}")
    elif land_pct > 0.40:
        out.append(f"* LAND COUNT: {mana['total_lands']} lands ({land_pct:.0%} of the deck) is on the "
                    f"heavy side for a deck with {mana['rock_count']} rocks and {mana['dork_count']} dorks "
                    f"on top ({source_count} effective sources, {source_pct:.0%}) -- there may be room to "
                    f"trim a land for another spell without hurting consistency much.")
    else:
        out.append(f"* LAND COUNT: {mana['total_lands']} lands ({land_pct:.0%}) plus {mana['rock_count']} "
                    f"rocks and {mana['dork_count']} dorks ({source_count} effective sources, "
                    f"{source_pct:.0%}) sits in the normal Commander range.")

    if len(color_avgs) >= 2:
        best_color = max(color_avgs, key=color_avgs.get)
        worst_color = min(color_avgs, key=color_avgs.get)
        gap = color_avgs[best_color] - color_avgs[worst_color]
        worst_sources = mana["land_color_copies"].get(worst_color, 0) + mana["rock_dork_color_copies"].get(worst_color, 0)
        best_sources = mana["land_color_copies"].get(best_color, 0) + mana["rock_dork_color_copies"].get(best_color, 0)
        if gap > 0.12:
            out.append(f"* COLOR IMBALANCE: {_SUMMARY_COLOR_NAMES[worst_color]} lags well behind "
                        f"{_SUMMARY_COLOR_NAMES[best_color]} ({_summary_pct(color_avgs[worst_color])} vs "
                        f"{_summary_pct(color_avgs[best_color])} average turn-of-CMC availability) -- "
                        f"{worst_sources} sources vs {best_sources}. If {_SUMMARY_COLOR_NAMES[worst_color]} "
                        f"cards are meant to come down on curve as reliably as "
                        f"{_SUMMARY_COLOR_NAMES[best_color]} ones, this deck wants more "
                        f"{_SUMMARY_COLOR_NAMES[worst_color]} fixing before more "
                        f"{_SUMMARY_COLOR_NAMES[worst_color]}-heavy cards.")
        else:
            out.append(f"* COLOR BALANCE: colors are reasonably even -- the gap between the strongest "
                        f"({_SUMMARY_COLOR_NAMES[best_color]}, {_summary_pct(color_avgs[best_color])}) and "
                        f"weakest ({_SUMMARY_COLOR_NAMES[worst_color]}, {_summary_pct(color_avgs[worst_color])}) "
                        f"is only {_summary_pct(gap)}. No color is being starved relative to the others.")
    elif len(color_avgs) == 1:
        (only_color, avg), = color_avgs.items()
        out.append(f"* MONO-COLOR: this is a one-color manabase ({_SUMMARY_COLOR_NAMES[only_color]}, "
                    f"{_summary_pct(avg)} average turn-of-CMC availability). There's no color balance to "
                    f"manage -- the only lever left for consistency is total source count and "
                    f"untapped-land ratio.")

    if curve_hist and nonland_count:
        peak_cmc = max(curve_hist, key=curve_hist.get)
        peak_n = curve_hist[peak_cmc]
        low_curve = sum(n for cmc, n in curve_hist.items() if cmc <= 2)
        high_curve = sum(n for cmc, n in curve_hist.items() if cmc >= 5)
        out.append(f"* CURVE SHAPE: peaks at CMC {peak_cmc} ({peak_n} cards, {peak_n/nonland_count:.0%} of "
                   f"all nonland spells). {low_curve} cards ({low_curve/nonland_count:.0%}) cost 2 or "
                   f"less; {high_curve} cards ({high_curve/nonland_count:.0%}) cost 5 or more. "
                   + (f"That's a top-heavy curve for a deck with {mana['total_lands']} lands -- expect "
                      f"a slower, grindier early game."
                      if high_curve / nonland_count > 0.30 else
                      f"That's a reasonably low, aggressive-leaning curve."))

    if mana["total_lands"] > 0:
        tapped_share = mana["tapped_count"] / mana["total_lands"]
        out.append(f"* LAND QUALITY: {mana['fetch_count']} fetchlands "
                    f"({mana['fetch_count']/mana['total_lands']:.0%} of lands, real deck-thinning) and "
                    f"{mana['tapped_count']} lands that can enter tapped ({tapped_share:.0%} of the "
                    f"manabase) -- "
                    + ("a meaningful tempo cost worth keeping in mind for turn-1/2 plays."
                       if tapped_share > 0.30 else "a manageable tempo cost."))

    chronic_names = [
        name for name, per_turn in results.items()
        if card_lookup.get(name) is not None
        and (card_lookup[name].cmc + MANA_AVAILABILITY_LOOKAHEAD) <= max_turns
        and per_turn.get(card_lookup[name].cmc + MANA_AVAILABILITY_LOOKAHEAD, 1) < 0.90
    ]
    if chronic_names:
        chronic_sorted = sorted(
            chronic_names,
            key=lambda n: results[n].get(card_lookup[n].cmc + MANA_AVAILABILITY_LOOKAHEAD, 1),
        )
        worst = chronic_sorted[0]
        worst_val = results[worst].get(card_lookup[worst].cmc + MANA_AVAILABILITY_LOOKAHEAD)
        out.append(f"* CONSISTENCY DRAINS: {len(chronic_names)} cards never reach 90% mana availability "
                    f"even {MANA_AVAILABILITY_LOOKAHEAD} turns past their own CMC -- worst is {worst} at "
                    f"{_summary_pct(worst_val)}. These are the cards most likely to sit dead in hand; "
                    f"they're prime candidates for either more targeted fixing or a straight cut if "
                    f"they're not pulling enough weight to justify it.")
    else:
        out.append(f"* CONSISTENCY: every nonland card in this list reaches at least 90% mana "
                    f"availability within {MANA_AVAILABILITY_LOOKAHEAD} turns of its own CMC -- no "
                    f"chronic color-screw risks in this build.")

    return out


def generate_statistics_summary(decklist_path: Path, commander_names: list, library_counts: dict,
                                 card_lookup: dict, results: dict, num_simulations: int,
                                 max_turns: int, mana_consistency: Optional[dict] = None) -> str:
    """
    Build the full text of a deckbuilder-facing statistics summary --
    an overall reliability heuristic, mana base breakdown, curve
    shape, color reliability index, the full per-card table, notable
    outliers, and a data-driven takeaways section -- from the same
    in-memory `results`/`card_lookup` `main()` already produced for
    the console table and --csv. Used by --summary.

    `mana_consistency` is the `{"screw_prob", "flood_prob"}` dict
    `run_simulation` returns alongside `results` -- optional (default
    None, rendered as "n/a") only so old call sites/tests that predate
    this stat don't break; every current caller passes it.
    """
    mana = _summary_mana_base(library_counts, card_lookup)
    curve_hist = _summary_curve_histogram(card_lookup, library_counts)
    color_rel = _summary_color_reliability(card_lookup, results)
    deck_avg = _summary_deck_average(library_counts, results, card_lookup)
    deck_stddev = _summary_deck_stddev(library_counts, results, card_lookup, deck_avg)
    mana_consistency = mana_consistency or {}

    total_cards = sum(library_counts.values())
    nonland_count = len(results)

    lines = []
    W = 78
    title = decklist_path.stem
    lines.append("=" * W)
    lines.append(f"{title.upper()} -- STATISTICS SUMMARY".center(W))
    lines.append("=" * W)
    lines.append("")
    lines.append(f"Source decklist : {decklist_path.name}")
    if commander_names:
        lines.append(f"Commander(s)    : {' // '.join(commander_names)}")
    else:
        lines.append("Commander(s)    : none declared -- no 'Commander' header found in the")
        lines.append("                  decklist file (see README for how to mark one)")
    lines.append(f"Library size    : {total_cards} cards ({mana['total_lands']} lands, "
                  f"{nonland_count} nonland spells)")
    lines.append(f"Simulation      : {num_simulations:,} games x {max_turns} turns, vibecodedCurveSim.py")
    lines.append("Metric          : mana-availability % per turn -- the odds your MANA BASE could")
    lines.append("                  pay a card's cost that turn, independent of whether you'd")
    lines.append("                  actually drawn the card yet. NOT a \"will I draw and cast this")
    lines.append("                  card\" probability.")
    lines.append("")

    def avg_or_dash(offset):
        return _summary_pct(deck_avg[offset]) if deck_avg[offset] is not None else "--"

    def stddev_or_dash(offset):
        return _summary_pct(deck_stddev[offset]) if deck_stddev.get(offset) is not None else "--"

    def consistency_or_dash(key):
        val = mana_consistency.get(key)
        return _summary_pct(val) if val is not None else "n/a (--max-turns too low)"

    lines.append(">>> OVERALL DECK RELIABILITY (headline figure -- use this to compare")
    lines.append("    different decks, or different versions of the same manabase, at a glance)")
    lines.append("")
    lines.append(f"    On a randomly picked nonland card (quantity-weighted across all")
    lines.append(f"    {nonland_count} of them), the average chance its cost was payable")
    lines.append(f"    ('+/-' is the standard deviation across cards -- how much that average")
    lines.append(f"    actually represents the whole deck, vs. being pulled around by a mix of")
    lines.append(f"    very safe and very shaky cards):")
    lines.append("")
    lines.append(f"      ON CURVE (turn == its own cmc):  {avg_or_dash(0):>6}  (+/- {stddev_or_dash(0)})")
    lines.append(f"      by cmc+1:                        {avg_or_dash(1):>6}  (+/- {stddev_or_dash(1)})")
    lines.append(f"      by cmc+2:                        {avg_or_dash(2):>6}  (+/- {stddev_or_dash(2)})")
    lines.append(f"      by cmc+3:                        {avg_or_dash(3):>6}  (+/- {stddev_or_dash(3)})")
    lines.append("")
    lines.append(f"    Mana consistency (deck-wide -- not tied to any one card):")
    lines.append(f"      Screw rate ({SCREW_LAND_THRESHOLD} or fewer lands in play by turn "
                  f"{SCREW_CHECK_TURN}):  {consistency_or_dash('screw_prob')}")
    lines.append(f"      Flood rate ({FLOOD_EXCESS_LAND_THRESHOLD}+ lands stuck unplayed in hand by "
                  f"turn {FLOOD_CHECK_TURN}): {consistency_or_dash('flood_prob')}")
    lines.append("")

    lines.append("-" * W)
    lines.append("1. MANA BASE")
    lines.append("-" * W)
    lines.append("")
    lines.append(f"  Total lands: {mana['total_lands']}  |  Fetchlands: {mana['fetch_count']}  |  "
                  f"Enters-tapped (any kind): {mana['tapped_count']}")
    lines.append(f"  Mana rocks: {mana['rock_count']}  |  Mana dorks: {mana['dork_count']}  |  "
                  f"Ramp spells: {mana['ramp_count']}")
    lines.append("")
    lines.append(f"  {'Color':<10} {'Land src':>9} {'Rock/Dork':>10} {'Total':>7}  Distribution")
    combined = {col: mana["land_color_copies"].get(col, 0) + mana["rock_dork_color_copies"].get(col, 0)
                for col in _SUMMARY_COLOR_ORDER}
    max_combined = max(combined.values()) if combined else 1
    for col in _SUMMARY_COLOR_ORDER:
        land_n = mana["land_color_copies"].get(col, 0)
        rd_n = mana["rock_dork_color_copies"].get(col, 0)
        tot = combined[col]
        if land_n == 0 and rd_n == 0:
            continue
        lines.append(f"  {_SUMMARY_COLOR_NAMES[col]:<10} {land_n:>9} {rd_n:>10} {tot:>7}  "
                      f"{_summary_bar(tot, max_combined)}")
    lines.append("")

    lines.append("-" * W)
    lines.append("2. SPELL CURVE SHAPE (nonland cards by CMC)")
    lines.append("-" * W)
    lines.append("")
    if curve_hist:
        max_curve = max(curve_hist.values())
        for cmc in sorted(curve_hist):
            n = curve_hist[cmc]
            lines.append(f"  CMC {cmc:>2}: {n:>3}  {_summary_bar(n, max_curve)}")
        avg_cmc = sum(cmc * n for cmc, n in curve_hist.items()) / sum(curve_hist.values())
        lines.append("")
        lines.append(f"  Average nonland CMC: {avg_cmc:.2f}")
    if total_cards:
        lines.append(f"  Land-to-spell ratio: {mana['total_lands']}:{nonland_count} "
                      f"({mana['total_lands'] / total_cards:.1%} of the deck is lands)")
    lines.append("")

    lines.append("-" * W)
    lines.append("3. COLOR RELIABILITY INDEX")
    lines.append("-" * W)
    lines.append("")
    lines.append("  Average turn-of-CMC mana availability among single-pip cards of each color")
    lines.append("  (e.g. a {W} or {2}{W} card counts; a {W}{W} or {W/U} card does not -- this")
    lines.append("  isolates \"how reliably does this color show up\" from pip-count difficulty).")
    lines.append("")
    if color_rel:
        max_avg = max(color_rel.values())
        for col, avg in sorted(color_rel.items(), key=lambda x: -x[1]):
            lines.append(f"  {_SUMMARY_COLOR_NAMES[col]:<10} {_summary_pct(avg):>7}  "
                          f"{_summary_bar(avg, max_avg)}")
    else:
        lines.append("  (no single-colored-pip nonland cards found to measure)")
    lines.append("")

    lines.append("-" * W)
    lines.append("4. FULL CURVE TABLE")
    lines.append("-" * W)
    lines.append("")
    lines.append(_summary_card_table(card_lookup, results))
    lines.append("")

    lines.append("-" * W)
    lines.append("5. NOTABLE OUTLIERS")
    lines.append("-" * W)
    lines.append("")

    def on_curve_val(name, card, per_turn):
        return per_turn.get(card.cmc)

    def gap_val(name, card, per_turn):
        oc = per_turn.get(card.cmc)
        c3 = per_turn.get(card.cmc + MANA_AVAILABILITY_LOOKAHEAD)
        return None if oc is None or c3 is None else c3 - oc

    def plateau_val(name, card, per_turn):
        return per_turn.get(card.cmc + MANA_AVAILABILITY_LOOKAHEAD)

    lowest_early = _summary_top_n(results, card_lookup, 5, on_curve_val, reverse=False,
                                   filter_fn=lambda n, c, p: c.cmc <= 4)
    lines.append("  Hardest to cast ON TIME (lowest on-curve %, cmc <= 4):")
    for v, name, card, per_turn in lowest_early:
        lines.append(f"    {_summary_pct(v):>6}  {name} ({card.mana_cost or 'land'}, cmc {card.cmc})")
    lines.append("")

    steepest = _summary_top_n(results, card_lookup, 5, gap_val, reverse=True)
    lines.append(f"  Steepest climbers (biggest on-curve -> cmc+{MANA_AVAILABILITY_LOOKAHEAD} gap -- "
                  f"color-hungry, reward patience):")
    for v, name, card, per_turn in steepest:
        lines.append(f"    +{_summary_pct(v):>5}  {name} ({card.mana_cost or 'land'}, "
                      f"{_summary_pct(per_turn.get(card.cmc))} -> "
                      f"{_summary_pct(per_turn.get(card.cmc + MANA_AVAILABILITY_LOOKAHEAD))})")
    lines.append("")

    most_reliable = _summary_top_n(results, card_lookup, 5, on_curve_val, reverse=True)
    lines.append("  Most reliable on turn of CMC (safest includes):")
    for v, name, card, per_turn in most_reliable:
        lines.append(f"    {_summary_pct(v):>6}  {name} ({card.mana_cost or 'land'}, cmc {card.cmc})")
    lines.append("")

    def chronic_filter(n, c, p):
        return (c.cmc + MANA_AVAILABILITY_LOOKAHEAD) <= max_turns and \
            p.get(c.cmc + MANA_AVAILABILITY_LOOKAHEAD, 1) < 0.90

    chronic_total = sum(1 for name, per_turn in results.items()
                         if card_lookup.get(name) is not None
                         and chronic_filter(name, card_lookup[name], per_turn))
    chronic = _summary_top_n(results, card_lookup, 8, plateau_val, reverse=False, filter_fn=chronic_filter)
    if chronic:
        label = (f"worst {len(chronic)} of {chronic_total}" if chronic_total > len(chronic)
                  else f"all {chronic_total}")
        lines.append(f"  Never reliable, < 90% mana availability even {MANA_AVAILABILITY_LOOKAHEAD} "
                      f"turns after CMC ({label}):")
        for v, name, card, per_turn in chronic:
            lines.append(f"    {_summary_pct(v):>6}  {name} ({card.mana_cost or 'land'}, cmc {card.cmc})")
        lines.append("")

    lines.append("-" * W)
    lines.append("6. DECKBUILDER TAKEAWAYS")
    lines.append("-" * W)
    lines.append("")
    lines.extend(_summary_takeaways(mana, curve_hist, color_rel, results, card_lookup,
                                     total_cards, nonland_count, max_turns))
    lines.append("")
    lines.append("=" * W)
    lines.append("Generated by vibecodedCurveSim.py --summary. See the module docstring at the top")
    lines.append("of this file for the full list of modeling assumptions and known limitations.")
    lines.append("=" * W)

    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Monte Carlo curve simulator for an MTG Commander decklist.",
    )
    parser.add_argument("decklist", type=Path, help="Path to a decklist text file")
    parser.add_argument("--simulations", type=int, default=10000,
                         help="Number of games to simulate (default: 10000)")
    parser.add_argument("--max-turns", type=int, default=MAX_SIMULATED_TURNS,
                         help=f"Turns to play out per game (default: {MAX_SIMULATED_TURNS})")
    parser.add_argument("--cache", type=Path, default=Path("scryfall_cache.json"),
                         help="Local Scryfall cache file (default: scryfall_cache.json)")
    parser.add_argument("--csv", type=Path, default=None, help="Optional CSV output path")
    parser.add_argument("--summary", type=Path, default=None,
                         help="Optional path to write a deckbuilder-facing statistics summary "
                              "(mana base, curve shape, color reliability, outliers, takeaways)")
    parser.add_argument("--verbose", action="store_true", help="Log each Scryfall fetch")
    args = parser.parse_args(argv)

    try:
        commander_names, library_counts = parse_decklist(args.decklist)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    all_names = set(library_counts) | set(commander_names)
    card_lookup = _build_card_lookup(all_names, args.cache, args.verbose)

    # Drop any card that couldn't be fetched from the simulated library
    # rather than crashing the run over one bad/misspelled line.
    for name in list(library_counts):
        if name not in card_lookup:
            library_counts.pop(name)
    if not library_counts:
        print("Error: no library cards could be resolved -- nothing to simulate.", file=sys.stderr)
        return 1

    results, mana_consistency = run_simulation(card_lookup, library_counts, args.simulations, args.max_turns)

    offsets = list(range(MANA_AVAILABILITY_LOOKAHEAD + 1))  # 0..3: cmc, cmc+1, cmc+2, cmc+3
    rows = sorted(results.items(), key=lambda kv: (card_lookup[kv[0]].cmc, kv[0]))
    print(f"\nSimulated {args.simulations} games, {args.max_turns} turns each.")
    print("Mana-availability %% by turn (cmc, then cmc+1..+%d) -- independent of "
          "whether the card was actually drawn.\n" % MANA_AVAILABILITY_LOOKAHEAD)
    header = f"{'CMC':>4}  " + "  ".join(f"{'cmc+' + str(o) if o else 'On-curve':>9}" for o in offsets) + "  Card"
    print(header)
    for name, per_turn in rows:
        cmc = card_lookup[name].cmc
        cells = []
        for o in offsets:
            turn = cmc + o
            cells.append(f"{per_turn[turn]:>9.1%}" if turn in per_turn else f"{'--':>9}")
        print(f"{cmc:>4}  " + "  ".join(cells) + f"  {name}")

    skipped = sorted(
        name for name, card in card_lookup.items()
        if name in library_counts and not card.is_land and not results.get(name)
    )
    if skipped:
        print(f"\nNot simulated (cmc exceeds --max-turns {args.max_turns}): {', '.join(skipped)}")

    if mana_consistency["screw_prob"] is not None:
        print(f"\nMana screw rate ({SCREW_LAND_THRESHOLD} or fewer lands in play by turn "
              f"{SCREW_CHECK_TURN}): {mana_consistency['screw_prob']:.1%}")
    if mana_consistency["flood_prob"] is not None:
        print(f"Mana flood rate ({FLOOD_EXCESS_LAND_THRESHOLD}+ lands stuck unplayed in hand by "
              f"turn {FLOOD_CHECK_TURN}): {mana_consistency['flood_prob']:.1%}")

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["card_name", "cmc"] + [f"turn_cmc+{o}" for o in offsets])
            for name, per_turn in rows:
                cmc = card_lookup[name].cmc
                row = [name, cmc]
                for o in offsets:
                    turn = cmc + o
                    row.append(f"{per_turn[turn]:.4f}" if turn in per_turn else "")
                writer.writerow(row)
        print(f"\nWrote {args.csv}")

    if args.summary:
        summary_text = generate_statistics_summary(
            args.decklist, commander_names, library_counts, card_lookup,
            results, args.simulations, args.max_turns, mana_consistency,
        )
        args.summary.write_text(summary_text, encoding="utf-8")
        print(f"\nWrote {args.summary}")

    return 0


if __name__ == "__main__":
    sys.exit(main())