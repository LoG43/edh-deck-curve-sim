# Changelog

This is the pass-by-pass development history of `vibecodedCurveSim.py`,
moved out of that file's module docstring (where it was Pass 2 through
Pass 15) to keep the source file itself shorter. It's a **historical
record**, not living documentation: each entry describes what was true
*at the time it was written*, including bugs that have since been fixed
and design decisions that have since been reversed. Entries are never
retroactively "corrected" to match the current state of the code — if
you want to know what the tool does *right now*, read the module
docstring's "KEY SIMPLIFYING ASSUMPTIONS" section and the code itself,
not this file.

This file began as a partial draft: decklist parsing, Scryfall
fetching/caching, and basic land/rock/dork/ramp classification were
written; the simulation engine was not.

## Pass 2 (data-model pass, engine still not written)

  - Card gained fetchland fields (`is_fetch_land`, `fetch_kind`,
    `fetch_subtypes`, `fetch_count`, `fetch_forces_tapped`), populated
    by the new `_parse_fetch_land()`. Previously a fetchland was
    classified as an ordinary land with `produced_colors = frozenset()`
    -- i.e. a land that produces NO mana at all under any
    circumstances, which is wrong. It is still an empty
    `produced_colors` today, but that is now a documented "resolve
    this via fetch_kind/fetch_subtypes at land-drop time" placeholder
    rather than an unexamined bug.
  - Card gained tapped-state fields (`tapped_kind`, `check_types`),
    populated by the new `_parse_tapped_kind()`. Previously there was
    no tapped-state concept at all -- every land was implicitly
    treated as untapped and ready the turn it entered, which is
    systematically optimistic for any deck running taplands.
  - `_parse_tapped_kind()` distinguishes unconditional taplands
    ("ALWAYS") from the conditional cycles ("SLOW", "FAST", "BOND",
    "BATTLE", "CHECK"), and additionally resolves CHECK and BATTLE
    lands -- previously considered not evaluable with the data on
    hand -- by extracting the named
    land subtypes (for check lands) straight out of the tapped land's
    own oracle text, since basic land types are printed on `type_line`
    for every land that has them (including non-basics like shocks),
    so "does the player control a Plains" is answerable from data
    Scryfall already gives us. Shocklands are detected separately and
    always resolve to "UNTAPPED" (life payment assumed paid, matching
    the original design decision).
  - ManaSource gained a `card: Optional["Card"]` back-reference, needed
    so a future land-drop step can inspect a permanent's identity
    (e.g. "is this land a Plains") --
    previously ManaSource only carried `colors`/`amount`/`ready` and
    threw that identity away.
  - The turn-by-turn engine itself (`can_pay_cost`, mulligan
    evaluation, land-drop selection, CSV export) is still not written.
    This pass only changes what a classified Card/ManaSource knows
    about itself.

## Pass 3 (scoping decisions, no behavior change)

  - Settled the file's name as `vibecodedCurveSim.py` -- earlier docs
    (including this docstring) referred to it as `mtg_curve_simulator.py`,
    which never matched the file on disk.
  - Made explicit (assumption 11 above) that life-total tracking
    (fetch/shock payments) and nonland enters-tapped/fetch-like effects
    are non-goals for this tool, not open gaps to eventually close --
    this is a manabase/mana-curve simulator, not a full life-total
    tracker. Mana rock and mana dork modeling (assumptions 1-2) is
    unchanged and unaffected.

## Pass 4 (the simulation engine -- the tool is now runnable end to end)

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

## Pass 5 (two real engine bugs, found by running against an actual 99-card decklist rather than synthetic test data)

Pass 4's synthetic validation exercised the turn loop's logic, but
couldn't have caught either of these, since neither is a turn-loop bug:

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

Both were caught by actually running the tool against a real decklist
and treating a suspicious result (a warning listing over half the deck
as "not found," and one card frozen at exactly 0.0% across 10,000
games) as a signal to investigate rather than as expected variance --
neither was visible in Pass 4's synthetic testing, which used small
hand-built decklists that never exercised real Scryfall rate limits or
a real MDFC.

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

## Pass 6 (land-drop tie-break fix)

Found by sanity-checking a specific card's on-curve rate against real
land/color-source counts rather than trusting the output number on its
own:

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

## Pass 7 (redefined the core statistic -- this changes what the tool answers, not a bug fix)

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

## Pass 8 (mana dorks were never actually deployed)

Found by tracing "how does the engine process rocks and dorks" rather
than trusting that classify_card's `is_mana_dork` flag implied
downstream handling:

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

## Pass 9 (double-faced-card field precedence)

Found running a second real decklist -- "Grave Researcher // Reanimate"
scored far below its cmc-3 peers, which was the tell:

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

## Pass 10 (MDFC-land modeling decision reversed)

A design change, not a bug fix; overrides the earlier "ignore back
faces" decision from Pass 3 for cards where a LAND face exists:

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

## Pass 11 (ramp-spell classification didn't check WHERE the fetched land goes)

Found while explaining, to the user, how ramp spells are counted and
measured, not from a suspicious result:

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

## Pass 12 (a genuine OPTIMISM bug)

The opposite of the "err pessimistic" audit that was asked for, found
by actually running that audit rather than assuming the answer:

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
        paid (module docstring assumption 3). This is NOT pessimistic
        -- it's an optimistic assumption in the
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

## Pass 13 (documentation accuracy only -- no behavior change)

Asked whether fetchlands were "still not modeled correctly" because
assumption 9 below still read "NOT YET resolved into actual mana --
there is no engine yet":

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
    clause is a known, unmodeled gap -- so this is accurately
    conservative, not broken). Prismatic Vista resolved untapped 100% of the time
    (correct: it never force-taps, and every land it can find is a
    basic, which always has `tapped_kind="UNTAPPED"`). Both match the
    real cards' rules text exactly.
  - No CSV or statistics summary needed regenerating -- this pass
    changed comments only, confirmed by the compile check and by the
    fact that the verification traces above exercised the EXISTING
    (unchanged) `resolve_fetch`/`resolve_tapped_state` code directly.

## Pass 14 (the deckbuilder-facing statistics summary is now a real, shippable feature -- `--summary`)

Previously it only existed as a one-off script run from outside this
repository entirely:

  - Every "statistics summary" file produced so far was generated by a
    separate script (`gen_reports.py`) that was never part of this
    codebase -- it lived in a session-local scratchpad directory
    outside the project, hardcoded to the four specific decklists it
    was written against (a display name and a manually-typed
    commander guess per deck). Nothing in `vibecodedCurveSim.py` could
    produce one; running the tool standalone (as reported: from
    PowerShell, independent of that session) correctly produced no
    such file, because the capability didn't exist here at all.
  - Ported that script's logic into this file as STEP 8
    (`generate_statistics_summary` and its `_summary_*` helpers) and
    wired it to a new `--summary PATH` flag, generalized to work on
    ANY decklist rather than the four it was hardcoded against: the
    report's title comes from the decklist's own filename, and its
    "Commander(s)" line reads the REAL `commander_names` parsed from
    the decklist (or says none were declared) instead of a hand-typed
    guess. Operates directly on the same in-memory `results`/
    `card_lookup` `main()` already built for the console table and
    `--csv` -- no intermediate CSV read-back, unlike the original
    script (which had to parse its own CSV output back into memory,
    since it ran as a separate process with no access to `main()`'s
    variables).
  - Sections 1-6 (overall reliability heuristic, mana base breakdown,
    curve histogram, color reliability index, full card table, notable
    outliers, data-driven takeaways) are a straight port -- verified
    end to end against a real decklist and diffed section-by-section
    against the original script's output for the same deck: identical.
    The header block is DELIBERATELY not identical: the original
    script's "Likely commander" line was a hand-typed guess (since it
    had no access to the real parse); this version reads the actual
    `commander_names` the decklist parser found and either lists them
    for real or says plainly that none were declared, which is more
    honest than a guess even though none of the four decklists this
    tool has been run against actually have a `Commander` header at
    all (a separate, real thing about those specific files, not a
    limitation of this flag).
  - All four decklists' statistics summaries (previously produced by
    the external script) were regenerated using this native flag, so
    the shipped feature and the files in the repo now come from the
    same code path.

## Pass 15 (land-drop heuristic ignored mana rocks/dorks/ramp spells' OWN color needs)

A real behavior change, not comments/docs; found by asking specifically
whether mana dorks shared the land-drop bug already identified for mana
rocks:

  - `_colors_needed_by_cheapest_uncastable` only ever searched
    `is_action_spell` cards for the cheapest not-yet-castable color
    need -- mana rocks, mana dorks, and ramp spells were excluded
    entirely, so their own color requirements never influenced which
    land got played, even though `deploy_accelerant` tries to cast the
    cheapest affordable one of them EVERY turn regardless. A hand
    holding, say, an uncastable 1-mana green dork and an off-color
    action spell would chase only the spell's color -- the land drop
    wasn't helping set up the very accelerant the engine was about to
    try casting right after it.
  - Two designs were considered: (a) unconditional strict priority for
    accelerants over action spells regardless of relative cmc, or
    (b) merge accelerants into the SAME cheapest-cmc search action
    spells already used, letting cmc alone decide -- no separate
    priority tier. Went with (b): a cheap dork/rock naturally wins when
    it's genuinely the cheapest not-yet-castable thing in hand (common,
    1-mana accelerants are common), without letting a pricier
    accelerant preempt a cheaper action spell's real need.
  - Fixed: candidate filter broadened from `c.is_action_spell` to
    `not c.is_land` (every nonland card). Ties still union colors
    across categories exactly as Pass 6 already did within action
    spells -- the same reasoning applies unchanged regardless of what
    category the tied cards belong to. Verified with three targeted
    cases matching the agreed design exactly: a cmc-1 dork beats a
    cmc-2 action spell's color; a cmc-1 action spell beats a cmc-3
    dork's color (cmc still decides, not category); a cmc-1 dork and a
    cmc-1 action spell of different colors union both colors. Also
    verified end to end through the real `play_land_drop` sort, not
    just the color-search helper in isolation.
  - This is a genuine simulation-behavior change (unlike Pass 13/14's
    comment/feature-only work) -- it can change which land gets played
    on any turn where an uncastable accelerant and an uncastable
    action spell of different colors are both in hand, for every
    decklist that runs mana rocks, dorks, or ramp spells. All four
    decklists processed by this tool run at least one such card, so
    all four were rerun and their CSVs/statistics summaries
    regenerated after this fix.
  - Left deliberately unaddressed, flagged rather than silently
    expanded into: `evaluate_hand_keepable` (the mulligan-keep trial)
    has the identical `is_action_spell`-only restriction when deciding
    what counts as a "spell cast" toward `MIN_SPELLS_TO_KEEP` -- a
    separate function, a separate decision (whether to keep a hand at
    all, not which land to play), and a broader behavioral question
    (should deploying a mana rock count toward hand keepability the
    same as casting a real spell?) that wasn't part of what was asked
    or agreed here.

## Pass 16 (backend cleanup ahead of a GitHub commit -- no behavior change)

Two low-risk, purely structural changes requested explicitly as
codebase cleanup, not as bug fixes or new functionality:

  - This changelog itself: the pass-by-pass history (previously Pass 2
    through Pass 15, embedded directly in `vibecodedCurveSim.py`'s
    module docstring) moved to this file, `CHANGELOG.md`, cutting the
    script's own line count by roughly a quarter. The module docstring
    now just points here. Content is unchanged, word for word, aside
    from converting "Pass N (...)" lines into markdown headers.
  - The Scryfall fetch/cache layer (`load_cache`, `save_cache`,
    `fetch_card_data`, `_scryfall_named`, `_throttle_scryfall_request`,
    `_is_rate_limit_error`, `_emit`, the `SCRYFALL_*` constants, and
    the `scrython`/`ScryfallError` import guard) moved out of
    `vibecodedCurveSim.py` into a new `scryfall_client.py` module. This
    was a genuinely clean, one-directional extraction: nothing in the
    fetch/cache layer ever depended on `Card`, `ManaCost`, or the
    simulation engine, and nothing outside `_build_card_lookup` called
    into it directly. `vibecodedCurveSim.py` now imports
    `load_cache`/`save_cache`/`fetch_card_data`/`_emit` from the new
    module; `app.py` needed zero changes, since it only ever called
    `sim._build_card_lookup` and never touched the Scryfall layer
    directly.
  - Verified via a full regression pass rather than assumed safe: a
    `py_compile` check on all three files, a CLI run (both plain and
    `--verbose`) against a real decklist producing byte-identical
    console/summary output to before the split, and a full web-app
    round trip through `flask.test_client()` (upload -> live SSE
    progress stream -> results page -> both download routes) confirmed
    working end to end.
  - What did NOT move, deliberately: the core engine (`Card`,
    `ManaCost`, `ManaSource`, mana-cost math, land-drop/fetch
    resolution, the turn loop) stays in one file. These pieces are
    tightly and legitimately coupled to each other; splitting them
    further would mean passing the same handful of objects between
    several files for no real readability gain, not a genuine
    separation of concerns like the Scryfall layer was.

## Pass 17 (a genuine OPTIMISM bug in costed mana rocks, a mulligan-test gap, docs, and a debug-mode fix -- found by an outside code review, not from a suspicious result)

Four independent fixes from the same review pass:

  - COSTED MANA ROCKS/DORKS WERE FREE: `_tap_ability_mana_count` only
    ever read the mana symbols after "Add" in a "{T}: Add ..." ability
    -- it never accounted for an EXTRA cost bundled into that same
    activation alongside the tap symbol. A guild Signet's "{1}, {T}:
    Add {U}{B}." was therefore credited with its full 2-mana output
    for free, every turn, forever -- the {1} it actually costs to
    activate was never subtracted anywhere. This is the same shape of
    bug as Pass 12's Pitiless Plunderer fix (an OPTIMISM bug, the
    opposite direction from this tool's usual "err pessimistic"
    approximations) but a different mechanism -- Pass 12's audit
    scanned for conditional/variable-amount language and never
    considered a FIXED extra cost, so it didn't catch this. Fixed:
    `_tap_ability_mana_count` (now backed by the new module-level
    `TAP_ABILITY_RE`) parses both the activation cost and the "Add"
    output, and returns `max(added - paid, 0)` -- a Signet nets +1
    mana (2 produced, 1 spent) instead of a phantom +2. Cost-free
    abilities (Sol Ring, Arcane Signet, Mind Stone, Fellwar Stone,
    Coalition Relic's free half) are unaffected, verified directly
    against all of their real oracle text. `Card.mana_per_tap`'s field
    comment and module docstring assumption 1 were both updated to
    describe net mana, not gross mana.
  - MULLIGAN KEEPABILITY IGNORED DEPLOYED ACCELERANTS: flagged as a
    known, deliberately deferred gap at the end of Pass 15 ("a
    separate function, a separate decision... that wasn't part of what
    was asked or agreed here") and left standing until now.
    `evaluate_hand_keepable`'s trial loop calls `deploy_accelerant`
    once per simulated turn, but discarded which card (if any) it
    actually played (`_, pool = deploy_accelerant(...)`) -- that
    played accelerant never counted toward `MIN_SPELLS_TO_KEEP`, even
    though the identical action-spell loop right below it did count.
    A hand that could easily deploy two castable mana rocks over four
    turns, but held zero action spells, was judged unkeepable purely
    because of this counting gap. First attempt at the fix (broadening
    the action-spell loop's own filter from `is_action_spell` to
    `not c.is_land`) was WRONG and caught by a synthetic regression
    test before landing: it would have let the trial deploy a SECOND
    accelerant in the same turn on top of the one `deploy_accelerant`
    already plays, breaking the "one accelerant per turn" rule this
    engine enforces everywhere else (assumption 4; `run_single_game`'s
    real turn loop has no equivalent second pass at all). Fixed
    correctly instead: capture `deploy_accelerant`'s return value and
    increment `spells_cast` when it played something, leaving the
    action-spell loop's own scope untouched. Verified with a synthetic
    hand of two accelerants and zero action spells (now keepable,
    previously wasn't) and a synthetic hand of exactly one accelerant
    (correctly still not keepable on its own).
  - DOCS: added assumption 15 to the module docstring and a matching
    card 15 on `assumptions.html`, both documenting that creature- and
    artifact-based land ramp (Solemn Simulacrum, Wayfarer's Bauble,
    Burnished Hart, Sword of the Animist, and similar cards) is not
    recognized as ramp at all -- `is_ramp_spell` requires `not
    is_artifact and not is_creature`, so these cards are checked only
    for their own casting cost, and the land they'd put into play
    contributes nothing to the simulated mana base. This was previously
    true (Pass 11 first noted the creature-exclusion half of it) but
    was never promoted out of buried changelog history into either of
    the two places a deckbuilder would actually look for it. No code
    behavior changed here -- documentation accuracy only, same as Pass
    13.
  - APP.PY NO LONGER RUNS WITH `debug=True`: Flask's debug mode ships
    the Werkzeug interactive debugger, which lets anyone who can reach
    an unhandled-exception page execute arbitrary Python in the
    browser. The app binds to localhost by default, which limits real-
    world exposure, but the README has brand-new users run `python
    app.py` directly as their normal way of using the tool, so
    shipping debug mode as the default -- a mode that exists for
    active development of app.py itself, not end use -- was
    unnecessary exposure. Changed to `debug=False`; `threaded=True` is
    unchanged and still required (see the comment above it).
  - Verified via `py_compile` on all three files, targeted synthetic
    (non-network) tests for the two behavior changes above (shown
    inline in this entry), and a full `run_simulation` smoke test on a
    synthetic decklist including both a Signet-style costed rock and
    Sol Ring side by side -- no crashes, plausible numbers for both.
