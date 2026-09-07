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
    CREATURES, not artifacts.

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
    not-yet-castable spell in hand.

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

None of these assumptions are exotic -- they mirror how experienced
deckbuilders reason about curves by hand -- but they are worth
knowing about before you treat the output as gospel.

--------------------------------------------------------------------
CHANGELOG
--------------------------------------------------------------------
This file began as a partial draft: decklist parsing, Scryfall
fetching/caching, and basic land/rock/dork/ramp classification were
written; the simulation engine was not. See DESIGN_NOTES.md for the
full handoff writeup this changelog tracks.

Pass 2 (data-model pass, engine still not written):
  - Card gained fetchland fields (`is_fetch_land`, `fetch_kind`,
    `fetch_subtypes`, `fetch_count`, `fetch_forces_tapped`), populated
    by the new `_parse_fetch_land()`. Previously a fetchland was
    classified as an ordinary land with `produced_colors = frozenset()`
    -- i.e. a land that produces NO mana at all under any
    circumstances, which is wrong (DESIGN_NOTES.md item 1). It is
    still an empty `produced_colors` today, but that is now a
    documented "resolve this via fetch_kind/fetch_subtypes at
    land-drop time" placeholder rather than an unexamined bug.
  - Card gained tapped-state fields (`tapped_kind`, `check_types`),
    populated by the new `_parse_tapped_kind()`. Previously there was
    no tapped-state concept at all -- every land was implicitly
    treated as untapped and ready the turn it entered (DESIGN_NOTES.md
    item 2), which is systematically optimistic for any deck running
    taplands.
  - `_parse_tapped_kind()` distinguishes unconditional taplands
    ("ALWAYS") from the conditional cycles ("SLOW", "FAST", "BOND",
    "BATTLE", "CHECK") per DESIGN_NOTES.md item 3, and additionally
    resolves CHECK and BATTLE lands -- which the design notes marked
    as "not evaluable with current data" -- by extracting the named
    land subtypes (for check lands) straight out of the tapped land's
    own oracle text, since basic land types are printed on `type_line`
    for every land that has them (including non-basics like shocks),
    so "does the player control a Plains" is answerable from data
    Scryfall already gives us. Shocklands are detected separately and
    always resolve to "UNTAPPED" (life payment assumed paid, matching
    the original design decision).
  - ManaSource gained a `card: Optional["Card"]` back-reference
    (DESIGN_NOTES.md item 4), needed so a future land-drop step can
    inspect a permanent's identity (e.g. "is this land a Plains") --
    previously ManaSource only carried `colors`/`amount`/`ready` and
    threw that identity away.
  - The turn-by-turn engine itself (`can_pay_cost`, mulligan
    evaluation, land-drop selection, CSV export) is still not written.
    This pass only changes what a classified Card/ManaSource knows
    about itself.

Pass 3 (scoping decisions, no behavior change):
  - Settled the file's name as `vibecodedCurveSim.py` -- earlier docs
    (including this docstring) referred to it as `mtg_curve_simulator.py`,
    which never matched the file on disk.
  - Made explicit (assumption 11 above) that life-total tracking
    (fetch/shock payments) and nonland enters-tapped/fetch-like effects
    are non-goals for this tool, not open gaps to eventually close --
    this is a manabase/mana-curve simulator, not a full life-total
    tracker. Mana rock and mana dork modeling (assumptions 1-2) is
    unchanged and unaffected.

Pass 4 (the simulation engine -- the tool is now runnable end to end):
  - Card gained a `mana_cost` field (the raw "{1}{U}{U}" string).
    Previously only `cmc` (an int) was stored, which can't distinguish
    a card's colored requirements from its generic cost -- needed to
    exist before any castability check could be written at all.
  - Added `ManaCost` / `parse_mana_cost` / `try_pay_cost` / `can_pay_cost`
    (STEP 4): the greedy most-constrained-first colored-mana matcher
    referenced but never defined in earlier drafts of this docstring
    (assumption 5).
  - Added `resolve_tapped_state`, `resolve_fetch`, and `play_land_drop`
    (STEP 5): this is where `tapped_kind`/`check_types` and
    `fetch_kind`/`fetch_subtypes`/`fetch_count`/`fetch_forces_tapped`
    (Pass 2's classification-only fields) finally get resolved against
    a real battlefield/library instead of just sitting on the Card.
  - Added `deploy_accelerant`, `check_on_curve_castability`,
    `evaluate_hand_keepable`, `choose_cards_to_bottom`,
    `draw_opening_hand`, `run_single_game`, and `run_simulation`
    (STEP 6): the turn-by-turn loop and mulligan procedure themselves,
    plus the per-card statistic aggregation. None of this existed
    before this pass.
  - Added a CLI (STEP 7: `_build_card_lookup`, `main`) so the file is
    now actually runnable per the USAGE section below, including CSV
    export.
  - Validated against synthetic (non-network) decklists rather than
    real Scryfall data: same-cost cards get statistically identical
    on-curve rates (no ordering/identity bias), and rates for
    singleton spells in a large pool track the hypergeometric
    draw-probability baseline closely once the deck has enough
    distinct spells for the mulligan rule to ever keep a hand (see the
    MULLIGAN LOOKAHEAD note below for why that caveat matters).

  Known limitations introduced or left standing by this pass:
    - MULLIGAN LOOKAHEAD: `MIN_SPELLS_TO_KEEP = 2` means a hand is
      only keepable if the deck has enough distinct nonland spells to
      ever produce 2 castable ones in a 7-card hand. A deck with very
      few nonland spells (pathological, but easy to construct by
      accident in a small test decklist) will mulligan to the legal
      maximum every game and then have the adaptive bottoming
      heuristic potentially discard the only spells in hand, since
      "bottom N cards from a land-heavy hand that also has almost no
      spells" runs out of lands to cut before it runs out of N. Any
      real ~99-card Commander decklist (which runs dozens of nonland
      spells) is nowhere near this edge, so it's noted here rather
      than defended against in code.
    - RAMP-SPELL LANDS AREN'T REAL CARDS: `deploy_accelerant` gives a
      ramp spell's fetched land a bare ManaSource with `card=None`
      (Pass 2's classify_card already only approximates a ramp spell's
      produced_colors as the deck's color identity, not a specific
      land). Consequence: `_battlefield_lands`, and therefore the
      SLOW/FAST/BATTLE conditional-tapland conditions, don't count
      lands that entered via a ramp spell as "other lands." In a
      ramp-heavy deck this can make a later SLOW/BATTLE land drop look
      tapped when the real game would have had enough lands in play
      for it to be untapped.
    - ZERO-CMC ACTION SPELLS are never checked: the turn loop starts
      at turn 1, so a card with cmc 0 (Mox-type effects, vanishingly
      rare in real Commander pools) never has a turn == cmc to be
      checked against, and `run_simulation` also excludes it via the
      `1 <= card.cmc` bound. Reported as "not simulated," not "0%."
    - can_pay_cost's greedy matcher (assumption 5) can, in principle,
      reject a payment a perfect constraint solver would have found --
      this was true before this pass as a documented approximation and
      remains true now that the matcher actually exists.

Pass 5 (two real engine bugs, found by running against an actual
99-card decklist rather than synthetic test data -- Pass 4's synthetic
validation exercised the turn loop's logic, but couldn't have caught
either of these, since neither is a turn-loop bug):
  - SCRYFALL RATE-LIMITING: fetch_card_data had no throttling between
    requests at all. A ~96-card decklist blew through Scryfall's <10
    req/s limit in under a second; every request Scryfall then 429'd
    was misread as "card not found" rather than "rate-limited," so 59
    of ~96 cards -- including Island, Mountain, Plains, and Sol Ring --
    were silently dropped from the simulated library. Fixed with
    `_throttle_scryfall_request` (a hard floor of
    SCRYFALL_MIN_REQUEST_INTERVAL between requests) and `_scryfall_named`
    (retries once with SCRYFALL_RATE_LIMIT_BACKOFF seconds of backoff
    if a 429 slips through anyway, instead of treating it as a miss).
  - CARD IDENTITY DRIFT FOR MDFCs: `check_on_curve_castability` records
    results keyed by `card.name`; `run_simulation` aggregates using
    `card_lookup`'s keys (the decklist spelling). These are usually
    the same string, but classify_card sets `card.name` from Scryfall's
    OWN `name` field, which for an MDFC is "Front // Back" -- e.g.
    Scryfall's name for "Sygg, Wanderwine Wisdom" (the decklist/front-
    face spelling) is "Sygg, Wanderwine Wisdom // Sygg, Wanderbrine
    Shield". Every successful cast was being recorded under the latter
    key, which `run_simulation` never reads, so the card showed a flat
    0% on-curve rate every single game despite being perfectly
    castable. Fixed in `_build_card_lookup`: after classification,
    `card.name` is forced to match the `card_lookup` key it's stored
    under, so every downstream lookup by name is self-consistent
    regardless of what Scryfall's own `name` field says.

  Both were caught by actually running the tool against a real
  decklist and treating a suspicious result (a warning listing over
  half the deck as "not found," and one card frozen at exactly 0.0%
  across 10,000 games) as a signal to investigate rather than as
  expected variance -- neither was visible in Pass 4's synthetic
  testing, which used small hand-built decklists that never exercised
  real Scryfall rate limits or a real MDFC.

  NOT a bug (logged and then reverted within this same pass, kept here
  as a correction for the record): the same test run also produced two
  cards Scryfall claimed not to recognize under their decklist names
  ("Minwu, Rebellion Strategist", "Shadowbringers"), which turned out
  to be Final Fantasy: Through the Ages flavor names for existing
  cards ("Mangara, the Diplomat", "Dovin's Veto" respectively) --
  `scrython.cards.Named(exact=...)` was correctly matching
  `flavor_name` as well as `name`, which is intended Scryfall behavior,
  not a defect. An intermediate version of this pass added a
  name-plausibility guard (`_name_plausibly_matches`, comparing only
  against `raw["name"]`) that didn't know about `flavor_name` and so
  incorrectly rejected these two legitimate matches; that guard has
  been removed entirely rather than patched, since there was never a
  real "wrong card silently substituted" failure mode to guard
  against in the first place -- `fetch_card_data` trusts `exact=`/
  `fuzzy=` results directly, as it always did before this pass.

Pass 6 (land-drop tie-break fix, found by sanity-checking a specific
card's on-curve rate against real land/color-source counts rather than
trusting the output number on its own):
  - `_colors_needed_by_cheapest_uncastable` used to return the colors
    of the FIRST not-yet-castable action spell found (by hand-
    iteration order) at the cheapest cmc tier, not all of them. When a
    hand held two different not-yet-castable spells tied for cheapest
    (e.g. a white 1-drop and a red 1-drop), `play_land_drop` would
    only ever chase ONE of their colors -- whichever happened to come
    first in hand order, an accident of draw order, not a deliberate
    priority -- leaving a matching land for the OTHER spell sitting
    unused in hand even when playing it would have helped, or even
    though a single dual land could have unlocked both. Measured on
    the real decklist this was diagnosed against: for a one-off {W}
    card, this was silently costing it a usable white land in hand
    roughly 7% of the games it was drawn in. Fixed: the function now
    returns the UNION of colors needed by ALL spells tied for cheapest,
    not just one, so `play_land_drop`'s scoring recognizes a land that
    helps any of them (and prioritizes one that helps several).
  - This was NOT caught by Pass 4's synthetic testing (which used
    small hand-built decklists unlikely to produce this kind of
    same-cost, different-color tie) or even by Pass 5's real-decklist
    run (which surfaced other bugs but didn't include this level of
    scrutiny). It surfaced only when a specific card's absolute
    on-curve percentage was checked against hand-computed expectations
    from the deck's actual mana base (land count, color-source counts
    per color) rather than accepted as plausible on its face.

Pass 7 (redefined the core statistic -- this changes what the tool
answers, not a bug fix):
  - Every prior pass's "on curve %" answered "was this specific card
    IN HAND and payable on turn == cmc," aggregated across all
    simulations. That conflates two different questions: "will I draw
    this card in time" and "can my mana base pay for it once I have
    it." For a 1-of card in a ~100-card library, the draw-probability
    half caps the number at roughly 8-9% turn-of-cmc regardless of how
    good the manabase is for that specific cost -- e.g. a `{W}` card
    scored ~5.5% even though, conditional on being drawn, it was
    payable ~64% of the time. The intent all along (see the module
    docstring's opening framing) was the mana-availability half alone.
  - Also scoped to only `is_action_spell` cards (excluding mana rocks,
    dorks, and ramp spells from the reported statistic), which is the
    wrong scope for a "does my mana base support this cost" question
    -- a signet's own `{2}` cost is just as valid a thing to ask about
    as an action spell's cost. Rescoped to every nonland card in the
    decklist.
  - `check_on_curve_castability` (hand-gated, single-turn) is replaced
    by `record_mana_availability` (no hand gate, checked for every
    turn in a card's [cmc, cmc + MANA_AVAILABILITY_LOOKAHEAD] window,
    MANA_AVAILABILITY_LOOKAHEAD = 3). `run_single_game` and
    `run_simulation` return `{card_name: {turn: probability}}` instead
    of `{card_name: probability}`; the CLI table/CSV now show one
    column per turn offset (cmc, cmc+1, cmc+2, cmc+3) instead of one
    number. `parse_mana_cost` is now called once per nonland card up
    front (`run_simulation` builds a `parsed_costs` dict) rather than
    repeatedly per game, since every nonland card is now checked every
    relevant turn instead of only cards actually drawn into hand.
  - What did NOT change: `play_land_drop`'s color-need heuristic and
    `evaluate_hand_keepable`'s mulligan gate are still driven by the
    ACTUAL hand each game (a simulated player only reacts to cards
    they're really holding) -- only the reported STATISTIC stopped
    requiring hand membership. The turn-by-turn mana pool a card's
    cost gets checked against is still the real, hand-driven pool that
    game; what changed is which cards get checked against it.
  - Verified against a synthetic deck before rerunning the real one:
    a colorless {1} artifact in a land-heavy shell showed 100% from
    turn 1 (correct -- trivially payable once any land is down,
    independent of the card ever being drawn), and a {1}{G} card
    showed a rising 77.5% / 89.8% / 95.7% / 98.4% curve across four
    turns (correct -- reflects the deck's green-source density
    improving as more lands hit play, not draw variance).

Pass 8 (mana dorks were never actually deployed -- found by tracing
"how does the engine process rocks and dorks" rather than trusting
that classify_card's `is_mana_dork` flag implied downstream handling):
  - `deploy_accelerant`'s candidate filter was `c.is_mana_rock or
    c.is_ramp_spell` -- `is_mana_dork` was never included. A mana dork
    was correctly CLASSIFIED, and (as of Pass 7) correctly given its
    own mana-availability statistic for its own casting cost, but it
    was never actually PLAYED as a permanent in the turn loop: it just
    sat in hand turn after turn, contributing no mana to any future
    turn's pool, no matter how many turns of trivially affordable mana
    went by. Verified directly: a 1-drop dork with a 30-land deck
    behind it and {G} open every turn from turn 1 onward stayed in
    hand, untouched, through turn 3 in a traced game.
  - Also surfaced in the same trace: `has_haste` is computed during
    classify_card but was only ever consumed by the ramp-spell tapped-
    land calculation -- it isn't a stored Card field, so there was no
    way for deploy_accelerant to have granted a hasty dork same-turn
    availability even if dorks HAD been included in the candidate list.
  - Fixed: `is_mana_dork` added to deploy_accelerant's candidate
    filter; a deployed dork gets a ManaSource with `ready=False`
    (summoning sickness, assumption 2) unless `"haste" in
    card.oracle_text.lower()`, re-checked at deploy time rather than
    adding a new stored field (Haste only matters for this one
    branch). Verified with a hasty and a non-hasty dork side by side:
    the hasty one's mana appeared in that turn's pool immediately, the
    other's `ManaSource.ready` was False until the following turn.
  - The real decklist this tool was being tested against has 9 mana
    rocks and zero mana dorks or ramp spells, so this bug happened to
    not affect any result reported so far for it -- but it would
    silently understate mana availability for any dork-reliant deck
    (Elfball, etc.) run through this tool before this fix.

Pass 9 (double-faced-card field precedence, found running a second real
decklist -- "Grave Researcher // Reanimate" scored far below its cmc-3
peers, which was the tell):
  - classify_card's MDFC handling only fell back to `card_faces[0]`'s
    own `mana_cost`/`type_line`/`oracle_text` when the TOP-LEVEL field
    was falsy (`not mana_cost`). That assumption is false for at least
    one Scryfall layout: `layout: "prepare"` (a newer double-faced
    mechanic) populates the top-level `mana_cost` as a "{front} //
    {back}" concatenation of BOTH faces -- non-empty, so the fallback
    never triggered. Concretely, Grave Researcher // Reanimate's
    top-level mana_cost was the STRING "{2}{B} // {B}"; MANA_SYMBOL_RE
    doesn't know about " // " as a separator, so parse_mana_cost read
    all three bracketed symbols as one cost (generic=2, colored={"B":2}
    -- effectively {2}{B}{B}, cmc 4) instead of the front face's real
    {2}{B} (cmc 3). The card was being checked against a cost one full
    black pip harder than it actually has.
  - Fixed: classify_card now prefers `card_faces[0]`'s own fields
    UNCONDITIONALLY whenever `card_faces` exists, not only when the
    top-level field happens to be empty. Scanned the entire shared
    Scryfall cache (~300+ unique cards across every decklist run
    through this tool so far) for any other card where top-level
    diverges from the front face: 13 found. For `mana_cost` specifically,
    12 of the 13 already had an EMPTY top-level field (correctly
    handled by the old fallback too) and only Grave Researcher's was
    non-empty-but-wrong. No cache purge needed for the mana_cost fix:
    the cached raw Scryfall data was never wrong, only classify_card's
    interpretation of it, and classify_card re-runs fresh from the
    cache every time rather than being cached itself.
  - The `type_line` half of this same fix turned out to matter MORE,
    and immediately -- caught by rerunning the same decklist rather
    than by the cache scan (which only checked whether fields diverged,
    not whether the divergence changed a classification outcome).
    Three cards -- Legion Leadership // Legion Stronghold, Sundering
    Eruption // Volcanic Fissure, Witch Enchanter // Witch-Blessed
    Meadow -- are all the "modal spell // land" cycle: an instant/
    sorcery/creature front face with a LAND back face. Their top-level
    type_line was a non-empty concatenation like "Instant // Land" --
    truthy, so the old fallback never triggered -- and `is_land =
    "Land" in type_line` matched the substring "Land" from the BACK
    face's type, misclassifying the front-face SPELL itself as a land.
    That's not a wrong number, it's complete disappearance: a card
    misclassified as `is_land` is excluded from `run_simulation`'s
    `not card.is_land` eligibility filter entirely, so all three were
    silently missing from the FFVI decklist's first results table with
    no warning at all, and would be from any other decklist running
    this "modal spell // land" cycle. Confirmed fixed: all three
    appear in the corrected rerun with plausible numbers in line with
    same-cmc, same-color peers.

Pass 10 (MDFC-land modeling decision reversed -- a design change, not
a bug fix; overrides the earlier "ignore back faces" decision from
Pass 3/DESIGN_NOTES.md for cards where a LAND face exists):
  - Previously (Pass 9): a double-faced card was always modeled as its
    FRONT face, whatever that was -- for the "modal spell // land"
    cycle (Legion Leadership // Legion Stronghold, etc.), that meant
    modeling the card as its spell side and ignoring that it can also
    just be played as a land. This matched an earlier explicit
    decision (this project's own history: "ignore back faces" was
    chosen over "model as a flex land" when this was first raised).
  - Changed on request: these cards are now modeled as THEIR LAND SIDE
    ONLY -- `classify_card` picks whichever face has "Land" in its
    type_line (if any) and uses ONLY that face's type_line/oracle_text/
    colors, with `mana_cost` forced to `""` and `cmc` forced to `0`
    (lands have neither). The spell face is no longer consulted at
    all for these cards. Cards with no land face at all (Grave
    Researcher, Sygg, Cecil, transform creatures generally) are
    unaffected -- still front-face-as-spell, per Pass 9.
  - Consequence: every card in the "modal spell // land" cycle moves
    from the nonland-spell table into the deck's land count. This
    changes land counts and color-source counts for every decklist
    that runs any of them -- confirmed via classify_card directly
    against every such card seen across all decklists processed by
    this tool so far (Legion Leadership, Sundering Eruption, Witch
    Enchanter, Hydroelectric Specimen, Razorgrass Ambush, Sink into
    Stupor, Pinnacle Monk, Glasswing Grace, Disciple of Freyalise --
    all now `is_land=True`, `cmc=0`, `produced_colors` matching their
    land face, and correctly excluded from the mana-availability
    table's card rows). `produced_mana` needed no fix alongside this:
    Scryfall populates it at the TOP level even for these cards (not
    per-face), and it already reflects the land face's actual colors.
  - Every decklist and CSV this tool has produced so far was
    recalibrated and overwritten after this change (see the session
    history for which files).

Pass 11 (ramp-spell classification didn't check WHERE the fetched land
goes -- found while explaining, to the user, how ramp spells are
counted and measured, not from a suspicious result):
  - LAND_RAMP_RE only matches "search your library for ... land card"
    -- it says nothing about what happens to the land afterward. A
    card that searches for a land and puts it into HAND (Prismatic
    Undercurrents: "...put them into your hand...") read identically
    to a real ramp spell under this regex, and was being classified
    `is_ramp_spell=True`. Consequence: if deploy_accelerant ever chose
    it as the turn's cheapest accelerant, it would add a phantom
    ManaSource straight to the battlefield for mana that doesn't exist
    in the real card -- Prismatic Undercurrents doesn't accelerate the
    battlefield at all, it just tutors basics into hand for you to
    play normally (it also grants an extra land drop per turn, which
    this tool has no way to model at all, since it always plays
    exactly one land per turn).
  - Fixed: added `_ramp_puts_onto_battlefield`, which requires the
    word "battlefield" to appear in the same sentence as the
    LAND_RAMP_RE match (up to the next period) before a card counts as
    ramp. Verified against real oracle text for both directions:
    Rampant Growth (battlefield, tapped) and a Cultivate-style split
    ("put one onto the battlefield ... and the other into your hand")
    both still classify as ramp; Prismatic Undercurrents no longer
    does. `_lands_fetched_count` (which only extracts the fetch-count
    number word) still uses the bare LAND_RAMP_RE search directly,
    since it's only ever called once `is_ramp_spell` is already True.
  - Separate, PRE-EXISTING gap noticed while testing this fix, left
    unaddressed (not what was asked, and inert against every decklist
    processed so far): LAND_RAMP_RE requires the literal phrase "land
    card" and never matches a card that names a specific basic type
    instead (Nature's Lore: "search your library for a Forest card").
    Confirmed via a scan across all four decklists processed by this
    tool that only one card is affected (Claim Jumper, in the FFVI
    decklist) -- and it wouldn't be reclassified even with that regex
    fixed, since it's a creature ETB trigger, and is_ramp_spell
    explicitly excludes creatures by design (only sorceries/instants
    qualify as the "ramp spell" bucket). Zero effect on any reported
    result; flagged here rather than silently left for later.
  - Only one of the four decklists processed by this tool has any
    ramp spells at all (B2 Bug Painsire: Shared Roots and, until this
    fix, Prismatic Undercurrents) -- that decklist's CSV and
    statistics summary were regenerated after this fix; the other
    three decklists have zero ramp spells and are unaffected.

Pass 12 (a genuine OPTIMISM bug -- the opposite of the "err pessimistic"
audit that was asked for, found by actually running that audit rather
than assuming the answer):
  - TAP_FOR_MANA_RE matches `{T}...: Add` anywhere in oracle_text,
    including inside PARENTHETICAL REMINDER TEXT that describes a
    TOKEN's ability, not the card's own. Pitiless Plunderer ("...create
    a Treasure token. (It's an artifact with '{T}, Sacrifice this
    token: Add one mana of any color.')") and Warren Soultrader (same
    Treasure-token reminder text) are both creatures that do NOT tap
    for mana themselves -- but the regex matched the token's reminder
    text anyway, so both were classified `is_mana_dork=True`. Consequence:
    `deploy_accelerant` would treat these as legitimate 1-mana dorks
    coming online the turn after being cast, which is fictional --
    neither ability taps the creature itself for mana at all; the real
    mana only exists if a Treasure token actually gets created (a
    separate trigger/cost involving OTHER creatures) and then THAT
    token is separately tapped. This directly overstates mana
    availability, the opposite of every other conditional found in
    this audit (see below).
  - Fixed: added PAREN_RE and strip parenthetical text before running
    TAP_FOR_MANA_RE. Verified safe before applying: scanned every
    currently-`is_mana_rock`/`is_mana_dork` card across all four
    decklists and confirmed each one's ability is ALSO stated outside
    parentheses -- Pitiless Plunderer and Warren Soultrader were the
    only two cards that depended on a parenthetical-only match, so
    this fix only removes false positives, never a true one.
  - The broader "err pessimistic" audit this fix came out of, for the
    record (requested after discussing conditional mana abilities):
    scanned every mana rock/dork across all four decklists for
    conditional or variable-amount language, and every land for BOND
    or SHOCK tapped_kind (the two land kinds resolved by a fixed
    assumption rather than a real per-game dynamic check). Findings:
      * Priest of Titania ("{T}: Add {G} for each Elf on the
        battlefield") parses to mana_per_tap=1 because the "for each"
        multiplier isn't bracket-notation and the regex only grabs the
        immediate {G} symbol -- but 1 IS the guaranteed floor (the
        card is itself an Elf), so this under-counts, never
        over-counts. Correctly pessimistic, if only by regex accident.
      * Sceptre of Eternal Glory's conditional 3-mana bonus ability
        ("Activate only if you control three or more lands with the
        same name") is entirely unparsed (word-spelled, no bracket
        notation) and always falls back to the 1-mana base rate --
        under-counts, never over-counts. Correctly pessimistic.
      * SHOCK-kind lands (Sacred Foundry, Steam Vents, Hallowed
        Fountain, Breeding Pool, Godless Shrine, and others) always
        resolve UNTAPPED, on the documented assumption the life is
        paid (module docstring assumption 3 / DESIGN_NOTES.md). This
        is NOT pessimistic -- it's an optimistic assumption in the
        mana-availability direction (it could be wrong in a life-
        pressured game), just one made deliberately and documented,
        not a parsing accident.
      * BOND-kind lands (Training Center, in the Homage to the Gwaf
        decklist) always resolve ready=True, on the documented
        assumption of >=2 opponents (true by default in a Commander
        pod). Also NOT pessimistic in the strict sense -- an
        assumption, not a computed worst case -- but this tool doesn't
        model opponents at all, so there's no alternative to assuming
        it either way.
      * Every "one mana of X" / "two mana of X" phrase found across
        all four decklists' rocks and dorks turned out to be a
        LITERAL fixed amount (the word IS the printed number, e.g.
        Arcane Signet's "Add one mana..." really only ever adds one),
        not a variable one -- correctly parsed as 1, not a pessimism
        concern at all.
    Net: every UNPARSED/approximated conditional found errs pessimistic
    (undercounts); the two non-pessimistic resolutions in the whole
    audit (SHOCK, BOND) are deliberate, documented modeling choices,
    not gaps -- and the one place this pass found a genuine over-count
    (Pitiless Plunderer / Warren Soultrader) wasn't a conditional-mana
    parsing issue at all, but a token-vs-card identity confusion, now
    fixed above.
  - Only Gisa's an Aristocrat, Right runs either affected card; its
    CSV and statistics summary were regenerated after this fix. The
    other three decklists are unaffected.

Pass 13 (documentation accuracy only -- no behavior change; asked
whether fetchlands were "still not modeled correctly" because
assumption 9 below still read "NOT YET resolved into actual mana --
there is no engine yet"):
  - That claim was true when written (Pass 2, before the engine
    existed) and false ever since Pass 4 wrote `resolve_fetch` -- it
    was simply never updated, a documentation-drift bug in itself, and
    a reasonable thing to worry was silently true. Assumptions 9 and 10
    below, and a matching comment on the Card dataclass's fetchland
    fields, all repeated the same stale "unwritten engine" framing;
    all three corrected to describe what `resolve_fetch` and
    `resolve_tapped_state` (STEP 5) actually do.
  - Verified the ENGINE itself is correct, not just that it exists:
    traced 500+ real fetch cracks in the Homage to the Gwaf decklist
    (its 2 fetches -- Fabled Passage, Prismatic Vista -- against its 6
    basic lands). Zero whiffs for either (6 basics in a 100-card
    library is still plenty within the first 5 turns). Fabled Passage
    resolved tapped 100% of the time (correct: it force-taps per
    `fetch_forces_tapped`, and its "untap if you control 4+ lands"
    clause is a known, separately-documented unmodeled gap -- see
    DESIGN_NOTES.md item 1 -- so this is accurately conservative, not
    broken). Prismatic Vista resolved untapped 100% of the time
    (correct: it never force-taps, and every land it can find is a
    basic, which always has `tapped_kind="UNTAPPED"`). Both match the
    real cards' rules text exactly.
  - No CSV or statistics summary needed regenerating -- this pass
    changed comments only, confirmed by the compile check and by the
    fact that the verification traces above exercised the EXISTING
    (unchanged) `resolve_fetch`/`resolve_tapped_state` code directly.

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
import json                 # reading/writing the local Scryfall cache file
import random                # shuffling the simulated library, drawing cards
import time                    # throttling outbound Scryfall requests
import re                     # pattern matching over card oracle text
import sys                     # exiting cleanly with error messages
import csv                      # optional CSV export of results
from collections import defaultdict   # convenient default-valued dicts
from dataclasses import dataclass, field  # clean, typed card records
from pathlib import Path                   # filesystem paths
from typing import Optional                 # optional type hints

try:
    import scrython
    from scrython.base import ScryfallError
except ImportError:
    # We only hard-fail on this once the user actually needs to hit the
    # network (see fetch_card_data). Importing scrython is deferred-ish
    # so that --help works even without the dependency installed, but
    # we still attempt the import up front to fail fast with a clear
    # message in the common case.
    scrython = None
    ScryfallError = Exception


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
# paid (see DESIGN_NOTES.md item 3), so shocks resolve to "UNTAPPED".
SHOCK_RE = re.compile(
    r"you may pay \d+ life\.\s*If you don't,\s*it enters tapped",
    re.IGNORECASE,
)

# Conditional taplands: "enters the battlefield tapped unless <condition>."
# Captures the condition clause so _parse_tapped_kind can classify it
# (slow/fast/bond/battle/check -- see DESIGN_NOTES.md item 3).
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

    is_land: bool = False
    is_mana_rock: bool = False    # nonland, noncreature artifact that taps for mana
    is_mana_dork: bool = False    # creature that taps for mana
    is_ramp_spell: bool = False   # sorcery/instant that fetches land(s)

    # How many individual mana "instances" a single tap of this
    # permanent produces (Sol Ring = 2, a basic land = 1, etc.)
    mana_per_tap: int = 1

    # What colors of mana this source can produce. For colorless-only
    # sources (Sol Ring, Mind Stone) this is frozenset({"C"}).
    produced_colors: frozenset = field(default_factory=frozenset)

    # For ramp spells only: how many lands they fetch, and whether the
    # fetched land(s) enter tapped (delaying availability by a turn).
    lands_fetched: int = 1
    fetched_lands_tapped: bool = ASSUME_RAMP_LANDS_TAPPED

    # --- Fetchland fields (DESIGN_NOTES.md item 1) ---------------------
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

    # --- Tapped-land fields (DESIGN_NOTES.md items 2 & 3) ---------------
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

    # Back-reference to the originating Card (DESIGN_NOTES.md item 4).
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
# STEP 2: FETCH CARD DATA FROM SCRYFALL (via scrython), WITH A LOCAL CACHE
# =======================================================================
def load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


# Scryfall's documented limit is <10 requests/second. scrython does
# NOT enforce this itself -- an earlier version of this comment
# claimed it did; that was untested and wrong. Verified the hard way:
# a ~96-card decklist with no throttling blew through the limit in
# well under a second and got most of the deck (including basics and
# Sol Ring) rejected as 429s, which fetch_card_data was then
# misreporting as "card not found." SCRYFALL_MIN_REQUEST_INTERVAL
# keeps us safely under that limit; the retry/backoff below is a
# safety net for if we still get rate-limited anyway.
SCRYFALL_MIN_REQUEST_INTERVAL = 0.15   # ~6.6 req/s, comfortably under 10/s
SCRYFALL_RATE_LIMIT_BACKOFF = 60        # seconds -- matches Scryfall's own message
SCRYFALL_MAX_RETRIES = 1

_last_scryfall_request_time = 0.0


def _throttle_scryfall_request() -> None:
    """Block until SCRYFALL_MIN_REQUEST_INTERVAL seconds have passed
    since the last outbound Scryfall request."""
    global _last_scryfall_request_time
    elapsed = time.monotonic() - _last_scryfall_request_time
    if elapsed < SCRYFALL_MIN_REQUEST_INTERVAL:
        time.sleep(SCRYFALL_MIN_REQUEST_INTERVAL - elapsed)
    _last_scryfall_request_time = time.monotonic()


def _is_rate_limit_error(exc: Exception) -> bool:
    return "rate-limited" in str(exc).lower() or "rate limit" in str(exc).lower()


def _scryfall_named(**kwargs):
    """
    One throttled `scrython.cards.Named` call. If Scryfall responds
    with an actual rate-limit error (as opposed to a genuine no-match),
    wait out SCRYFALL_RATE_LIMIT_BACKOFF and retry once before giving
    up -- distinguishing this from a real miss matters because the
    caller (fetch_card_data) falls back to a fuzzy-name search on any
    ScryfallError, which is the wrong response to a 429.
    """
    last_exc = None
    for attempt in range(SCRYFALL_MAX_RETRIES + 1):
        _throttle_scryfall_request()
        try:
            return scrython.cards.Named(**kwargs)
        except ScryfallError as exc:
            last_exc = exc
            if _is_rate_limit_error(exc) and attempt < SCRYFALL_MAX_RETRIES:
                print(f"  [fetch_card_data] Rate-limited by Scryfall; waiting "
                      f"{SCRYFALL_RATE_LIMIT_BACKOFF}s before retrying...", file=sys.stderr)
                time.sleep(SCRYFALL_RATE_LIMIT_BACKOFF)
                continue
            raise
    raise last_exc


def fetch_card_data(name: str, cache: dict, verbose: bool = False) -> Optional[dict]:
    """
    Return the raw Scryfall JSON dict for `name`, using the on-disk
    cache when possible and only hitting the network for cards we
    haven't seen before. Returns None if the card genuinely can't be
    found (NOT if we were rate-limited -- see _scryfall_named).

    Trusts `exact=`/`fuzzy=` results as-is (no extra name-matching
    validation here -- see CHANGELOG Pass 5). scrython's `exact=`
    search matches a card's `flavor_name` as well as its mechanical
    `name`, which is correct, intended Scryfall behavior: Universes
    Beyond crossovers reprint existing cards under new flavor names
    (e.g. "Minwu, Rebellion Strategist" is a Final Fantasy: Through
    the Ages flavor name for the mechanically-identical "Mangara, the
    Diplomat"), and `exact=` resolving that is the correct outcome,
    not a bug to guard against.
    """
    cache_key = name.lower()
    if cache_key in cache:
        return cache[cache_key]

    if scrython is None:
        raise RuntimeError(
            "The 'scrython' package is required to fetch card data. "
            "Install it with:  pip install scrython"
        )

    try:
        result = _scryfall_named(exact=name)
    except ScryfallError:
        try:
            result = _scryfall_named(fuzzy=name)  # fall back for typos/abbreviations
        except ScryfallError as exc:
            print(f"  [fetch_card_data] Could not find card {name!r} on "
                  f"Scryfall: {exc}", file=sys.stderr)
            return None

    raw = dict(result._scryfall_data)
    cache[cache_key] = raw

    if verbose:
        print(f"  [fetch_card_data] Fetched {raw.get('name', name)!r} from Scryfall")

    return raw


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
    Estimate how many mana instances a single tap-for-mana ability
    produces by looking at the symbols right after "Add" in the
    sentence containing "{T}: Add". Defaults to 1 if we can't parse it
    (true for the vast majority of mana rocks/dorks/lands, which
    produce exactly one mana per tap).
    """
    match = re.search(r"\{T\}[^.]*?Add\s+((?:\{[^}]+\})+)", oracle_text or "", re.IGNORECASE)
    if not match:
        return 1
    symbols = _extract_mana_symbols(match.group(1))
    # Symbols like "{C}{C}" -> 2 instances. A symbol that's a number
    # (rare here, but defensive) would mean "add N mana of any type" --
    # treat that as N instances too.
    total = 0
    for sym in symbols:
        total += int(sym) if sym.isdigit() else 1
    return max(total, 1)


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

    # Fetchland / tapped-state classification (DESIGN_NOTES.md items
    # 1-3). Only meaningful for lands; nonland permanents don't have
    # this kind of battlefield-entry state in scope for this tool.
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
    doesn't do (see DESIGN_NOTES.md / assumption 5).
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
    The color(s) required by the cheapest not-yet-castable action
    spell(s) in `hand`. Drives both land-drop and fetch-target
    selection (assumption 6: chase the colors the next thing you
    actually want to cast needs, not color diversity in general).

    When multiple not-yet-castable spells TIE for cheapest, this
    returns the UNION of all their colors, not just one arbitrarily
    picked spell's. Only tracking a single spell's colors here was a
    measured, real limitation: whichever spell happened to be first in
    hand-iteration order (an accident of draw order, not a deliberate
    priority) would win the land drop every time, silently starving
    same-cost siblings of a matching land even when one was in hand.
    Concretely: for a one-off {W} card in a hand that also held any
    other not-yet-castable cmc-1 spell, this cost it a matching white
    land roughly 7% of the time it was drawn, in testing against a
    real 3-color decklist -- a land that could have made both colors
    (e.g. an R/W dual matching a white AND a red 1-drop) was being
    ignored in favor of whichever spell won the coin-flip. Aggregating
    the tied tier lets `play_land_drop`'s scoring recognize a land
    that helps ANY of them, including one that helps all of them.
    """
    candidates = sorted((c for c in hand if c.is_action_spell), key=lambda c: c.cmc)
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
      1. hits a color the cheapest not-yet-castable spell in hand needs,
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
    (no further draws), can the player cast at least
    MIN_SPELLS_TO_KEEP distinct nonland spells within
    MULLIGAN_LOOKAHEAD_TURNS turns? Spells are actually deployed
    (mana spent, card removed from the trial hand) as they become
    affordable, turn by turn in ascending-cmc order, so two spells
    can't both "count" off mana that could only pay for one of them.

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
        _, pool = deploy_accelerant(trial_hand, trial_battlefield)

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
                     max_turns: int) -> dict:
    """
    Simulate one full game: mulligan to a kept hand, then play out
    `max_turns` turns (draw, land drop, one accelerant, mana-
    availability checks). Returns `{card_name: {turn: bool}}` for
    every NONLAND card and every turn in its curve window (see
    record_mana_availability) -- whether that specific mana pool that
    turn could pay the card's cost, independent of whether the card
    was actually drawn this game.

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

    for turn in range(1, max_turns + 1):
        if library:
            hand.append(library.pop(0))

        play_land_drop(hand, library, battlefield)
        _, pool = deploy_accelerant(hand, battlefield)
        record_mana_availability(card_lookup, parsed_costs, pool, turn, results)

        for source in battlefield:
            source.ready = True

    return results


def run_simulation(card_lookup: dict, library_counts: dict, num_simulations: int,
                    max_turns: int = MAX_SIMULATED_TURNS) -> dict:
    """
    Run `num_simulations` independent games and aggregate, per NONLAND
    card in the decklist (mana rocks, dorks, ramp spells, and action
    spells alike -- everything that isn't a land) and per turn in its
    curve window [cmc, cmc + MANA_AVAILABILITY_LOOKAHEAD], the fraction
    of games in which the mana existed that turn to pay its cost.

    This is a mana-base question, not a "will I draw this" question
    (Pass 7): the denominator is `num_simulations`, but unlike the
    pre-Pass-7 metric this no longer requires the card to have been
    drawn that game -- every game's actual land/accelerant sequencing
    (driven by whatever WAS drawn) produces a pool each turn, and every
    nonland card's cost is checked against that pool regardless of
    whether the card itself showed up in hand. "Could my mana base pay
    for a {1}{U}{U} spell by turn 4" no longer gets capped at ~8% by
    the odds of drawing one specific copy of it.

    Returns `{card_name: {turn: probability}}`. A card whose cmc
    exceeds `max_turns` has an empty inner dict (its window never
    overlaps a simulated turn) -- the caller should report that
    distinctly rather than reading it as "0% available."
    """
    eligible_names = [
        name for name, card in card_lookup.items()
        if not card.is_land and name in library_counts
    ]
    parsed_costs = {name: parse_mana_cost(card_lookup[name].mana_cost) for name in eligible_names}
    turn_counts = defaultdict(lambda: defaultdict(int))

    for _ in range(num_simulations):
        results = run_single_game(card_lookup, library_counts, parsed_costs, max_turns)
        for name, per_turn in results.items():
            for turn, ok in per_turn.items():
                if ok:
                    turn_counts[name][turn] += 1

    return {
        name: {turn: turn_counts[name][turn] / num_simulations
               for turn in range(max(1, card_lookup[name].cmc),
                                  max(1, card_lookup[name].cmc) + MANA_AVAILABILITY_LOOKAHEAD + 1)
               if turn <= max_turns}
        for name in eligible_names
    }


# =======================================================================
# STEP 7: CLI
# =======================================================================
def _build_card_lookup(names: set, cache_path: Path, verbose: bool = False) -> dict:
    """
    Fetch + classify every card in `names` (decklist spelling -- e.g.
    "Sol Ring" as typed in the deck file, NOT necessarily Scryfall's
    canonical capitalization). Returns {decklist_name: Card}, skipping
    (and warning about) any name Scryfall couldn't resolve. Caches to
    `cache_path`, saved once at the end regardless of how many cards
    were newly fetched.
    """
    cache = load_cache(cache_path)
    raws = {}
    for name in sorted(names):
        raw = fetch_card_data(name, cache, verbose=verbose)
        if raw is not None:
            raws[name] = raw
    save_cache(cache_path, cache)

    missing = sorted(names - raws.keys())
    if missing:
        print(f"Warning: {len(missing)} card(s) not found on Scryfall, excluded from "
              f"the simulation: {', '.join(missing)}", file=sys.stderr)

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

    results = run_simulation(card_lookup, library_counts, args.simulations, args.max_turns)

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

    return 0


if __name__ == "__main__":
    sys.exit(main())