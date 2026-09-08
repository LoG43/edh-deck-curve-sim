# MTG Commander Curve Simulator

A tool for Magic: The Gathering Commander (EDH) deckbuilders. You give it a
decklist; it plays out **10,000 simulated games** of your deck and tells you,
for every single nonland card, how reliably your mana base can actually pay
for it — turn by turn.

This page assumes you've never used Python, a terminal, or GitHub before.
Every step is spelled out. If you're already comfortable with all of that,
skip to [Quick Start](#quick-start).

## What does this actually tell me?

For every nonland card in your deck (creatures, spells, mana rocks — all of
it), you get a number like this:

```
 CMC   On-curve      cmc+1      cmc+2      cmc+3  Card
   3      61.2%      85.4%      92.6%      95.2%  Teferi, Time Raveler
```

Read this as: *"Across 10,000 simulated games, in 61.2% of them, this deck's
mana base could actually pay {1}{W}{U} by turn 3 (Teferi's own mana value).
By turn 6 (cmc+3), that climbs to 95.2%."*

**Important: this is not "how likely am I to draw this card."** It answers a
narrower, more useful question for deckbuilding: *if* this card is in your
hand, does your manabase actually support its colors and cost by the turn it
should come down? A card that scores low here isn't unlucky to draw — its
colors are undersupported relative to its cost, which is something you can
actually fix by changing the deck.

This lets you spot things like:

- **A weak color.** If every red card in your deck reliably lags behind your
  blue cards, your red sources are probably too thin.
- **An overcosted color intensity.** A double-pip card in your worst color is
  often the single hardest thing in the whole deck to cast on time — even if
  its mana value looks modest.
- **Whether your land count and fixing are actually enough**, instead of
  guessing from "the rule of thumb says 37 lands."

It does **not** replace playtesting, and it doesn't know about board states,
combat, or what your opponents are doing — see [What This Tool Doesn't Do](#what-this-tool-doesnt-do)
below.

## Quick Start

```
pip install -r requirements.txt
python vibecodedCurveSim.py your_decklist.txt
```

That's the whole thing, if you already have Python and a decklist text file.
Everyone else, keep reading.

---

## Step 1 — Install Python

You need Python installed on your computer. If you're not sure whether you
already have it, open a terminal (see Step 3 below for how) and type:

```
python --version
```

If that prints something like `Python 3.11.4`, you're already set — skip to
Step 2.

If it says "command not found" or similar:

1. Go to **[python.org/downloads](https://www.python.org/downloads/)** and
   download the installer for your operating system.
2. Run the installer.
   - **Windows:** on the very first screen, check the box that says
     **"Add python.exe to PATH"** before clicking Install. This is the most
     commonly missed step — if you skip it, your computer won't know how to
     find Python from a terminal.
   - **Mac:** the installer handles this automatically.
3. Close and reopen any terminal windows, then check `python --version`
   again to confirm it worked. (On some systems the command is `python3`
   instead of `python` — if `python --version` doesn't work, try
   `python3 --version`.)

## Step 2 — Download this tool

If you're viewing this on GitHub:

1. Click the green **Code** button near the top of the repository page.
2. Click **Download ZIP**.
3. Once it's downloaded, extract/unzip it somewhere you'll remember (like
   your Desktop or Documents folder).

(If you're comfortable with Git, `git clone` works too, but the ZIP download
is simpler if you've never used Git.)

## Step 3 — Open a terminal in that folder

A "terminal" (also called a command prompt or shell) is just a window where
you type commands instead of clicking things. You'll use it to install the
one thing this tool depends on, and to actually run it.

- **Windows:** open the extracted folder in File Explorer, then hold
  **Shift** and **right-click** in an empty area inside the folder. Choose
  **"Open PowerShell window here"** (or **"Open in Terminal"** on Windows 11).
- **Mac:** open the extracted folder in Finder, right-click it, choose
  **Services > New Terminal at Folder**. (If that option isn't there, open
  the **Terminal** app from Applications > Utilities, type `cd ` — with a
  trailing space — then drag the folder into the Terminal window and press
  Enter.)
- **Linux:** most file managers have a "Open Terminal Here" option in the
  right-click menu.

You should now have a terminal window whose current location is the folder
containing `vibecodedCurveSim.py`.

## Step 4 — Install the one dependency

This tool needs one additional piece of software, called `scrython`, to
fetch card data from [Scryfall](https://scryfall.com) (a free, public
Magic card database). In the terminal you just opened, type:

```
pip install -r requirements.txt
```

and press Enter. You'll see some text scroll by as it downloads and
installs; when it's done, you're ready to go. (If `pip` isn't recognized,
try `pip3` instead, or `python -m pip install -r requirements.txt`.)

## Step 5 — Get your decklist as a plain text file

This is the part that trips people up most, so read carefully.

The tool needs a plain `.txt` file where **every line is a quantity followed
by a card name**, like this:

```
1 Sol Ring
1 Command Tower
38 Forest
1 Rampant Growth
```

**Where to get this file:** almost every deckbuilding website (Moxfield,
Archidekt, EDHREC, TappedOut, Commander Spellbook, etc.) has an "Export" or
"Copy to clipboard" option that gives you exactly this format, or something
very close to it. Copy that text into a new file, save it with a `.txt`
extension (e.g. `my_deck.txt`), and put it in the same folder as
`vibecodedCurveSim.py` (or just remember where you saved it).

A few things the tool handles automatically, so don't worry about cleaning
these up by hand:

- **Set codes and collector numbers** at the end of a line, like
  `1 Sol Ring (C21) 263`, are stripped automatically — export formats that
  include these are fine as-is.
- **Blank lines and comments** (lines starting with `#` or `//`) are
  ignored, so you can leave yourself notes.

**One thing you should do by hand — mark your Commander.** Most exports list
your commander as just another card in the list, which means the tool will
treat it like any other card you might or might not draw, instead of
something you can always cast from the command zone. To fix this, add a
`Commander` line above your commander (and, optionally, a `Deck` line above
the rest):

```
Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
1 Command Tower
...
```

If your list has partner commanders or a background, put both under the
`Commander` heading. One side effect of doing this correctly: your commander
will then **disappear from the results table entirely** rather than getting
a row — that's expected, not a bug. It means the tool excluded it from the
simulated deck, exactly as intended, since it's cast from the command zone,
not drawn.

**If you skip this step, the tool will still run** —
it'll just treat your commander(s) as ordinary library cards instead of
cards you always have access to from the command zone. In practice this is a
fairly small effect (mostly: the library becomes 100 cards instead of 99,
and your commander occupies a hand slot in the simulation instead of being
free), but it's a real one, and free to avoid. Worth the extra 30 seconds.

## Step 6 — Run it

Back in your terminal (still in the tool's folder), type:

```
python vibecodedCurveSim.py my_deck.txt
```

(replacing `my_deck.txt` with whatever you named your file — if you saved it
somewhere else, use the full path instead, e.g.
`python vibecodedCurveSim.py "C:\Users\You\Desktop\my_deck.txt"`.)

**What happens next:** the first time you run any given decklist, the tool
looks up every card on Scryfall over the internet, one at a time (it
deliberately paces itself to avoid overloading Scryfall's servers, so a
~100-card deck can take **30–90 seconds** the first time — this is normal,
just let it finish). Every card it looks up gets saved to a local file
(`scryfall_cache.json`), so running the *same* deck again — or a different
deck that shares a lot of cards with one you've already run — is much
faster.

After that, it plays out 10,000 simulated games and prints a table like the
one shown at the top of this page.

### Useful optional flags

Add these after the decklist filename, e.g.
`python vibecodedCurveSim.py my_deck.txt --csv results.csv`:

| Flag | What it does |
|---|---|
| `--csv results.csv` | Also save the raw results to a spreadsheet-friendly CSV file, so you can open it in Excel, Google Sheets, or Numbers. |
| `--summary report.txt` | Also save a deckbuilder-facing statistics summary — mana base breakdown, curve shape, a color reliability index, an overall reliability heuristic for comparing decks at a glance, and a plain-language takeaways section. See [Step 8](#step-8--the-statistics-summary-report) below. |
| `--simulations 20000` | Run more simulated games (default 10,000) for a more precise answer, at the cost of taking longer to run. |
| `--max-turns 12` | Simulate further into the game (default 10 turns) — useful if your deck has a lot of very expensive cards. |
| `--verbose` | Print a line for every single card as it's looked up on Scryfall, so you can watch progress on a big decklist. |

Run `python vibecodedCurveSim.py --help` any time to see this list from the
tool itself.

## Step 7 — Reading your results

The table has one row per nonland card, sorted by mana value:

```
 CMC   On-curve      cmc+1      cmc+2      cmc+3  Card
   1      85.2%      99.9%      99.9%    100.0%  Sol Ring
   1      37.9%      69.6%      90.8%     96.4%  Condemn
```

- **CMC** — the card's mana value (what turn it "should" come down on).
- **On-curve** — the percentage of simulated games where the mana was
  available on exactly that turn.
- **cmc+1 / cmc+2 / cmc+3** — the same question, one/two/three turns later.
  A card that climbs quickly toward 100% just needed the deck to develop a
  bit; a card still stuck in the 60-70% range three turns later has a real
  color or cost problem, not just bad luck in the sample.

A few patterns worth knowing how to read:

- **Colorless cards (artifacts, generic-cost spells) score very high, very
  early.** That's correct — they don't care about your color balance at all,
  only about having *any* land in play. Sol Ring above is a good example.
- **Cards needing a rare color in your manabase score lower, and climb more
  slowly.** That's the tool doing its job — it's telling you that color is
  undersupported.
- **Double- or triple-pip costs in your weaker colors are usually the worst
  offenders.** If a card needs `{W}{W}` and white is your third color, it
  will often be the single hardest card in your deck to cast on time — worth
  knowing before you build around it.

If you exported a CSV (`--csv results.csv`), you can open that same data in
any spreadsheet program to sort, filter, or chart it — for example, sorting
by the "On-curve" column to see your least-reliable cards at a glance.

## Step 8 — The Statistics Summary Report

Running with `--summary report.txt` writes a second, plain-text file aimed
squarely at deckbuilding decisions rather than raw data. It includes:

- **An overall reliability heuristic** — a single headline percentage: if you
  reached into your deck and pulled one nonland card out at random, what's
  the average chance its cost was payable on curve? This is the single most
  useful number for comparing two different decks, or two versions of the
  same manabase before/after a change — a straightforward "did this get
  better or worse."
- **A mana base breakdown** — land count, fetch/tapped-land counts, and a
  per-color source count (lands and mana rocks/dorks counted separately).
- **A curve shape chart** — how many nonland cards sit at each mana value.
- **A color reliability index** — which of your colors is actually the
  weakest link, isolated from how many pips a card needs.
- **The full per-card table**, plus call-outs for your hardest-to-cast
  cards, your most "color-hungry" cards (the ones that take the longest to
  become reliable), your safest includes, and any cards that never become
  reliable at all.
- **A plain-language takeaways section** — a handful of bullet points
  (land count adequacy, color balance, curve shape, land quality, chronic
  problem cards) written directly from the numbers above it, so you don't
  have to do that reading yourself.

This is meant to be read top-to-bottom as a deckbuilding report, not just a
data dump — use it after making a change to your manabase to see whether the
headline number and the takeaways moved the way you expected.

## Troubleshooting

**"scrython is required..."** — you skipped or need to redo Step 4:
`pip install -r requirements.txt`.

**"Could not find card 'X' on Scryfall"** — almost always a typo in your
decklist file, or a card name Scryfall doesn't recognize under that exact
spelling. Double-check the spelling (including punctuation like commas and
apostrophes) against Scryfall's website. The tool will skip that one card
and keep going rather than stopping the whole run.

**"Rate-limited by Scryfall; waiting 60s before retrying..."** — this is
normal and handled automatically, especially on a very large decklist or if
you're running several decks back-to-back. Just let it wait; it'll continue
on its own.

**It looks stuck / nothing is happening** — on the *first* run of a new
decklist, it's fetching every card from the internet one at a time (on
purpose, to be polite to Scryfall's servers). For a 100-card deck this can
take up to a minute or two. Add `--verbose` to see it working card by card if
you want visible progress.

**My commander still shows up as a row in the results** — after adding the
`Commander` heading correctly (Step 5), your commander should disappear from
the output entirely, not show improved numbers — it's excluded from the
simulated deck altogether, since you already have guaranteed access to it
from the command zone. If it's still showing up as a row, double-check the
heading is spelled exactly `Commander`, on its own line, directly above your
commander's line.

## How the Simulated Player Makes Decisions

Every one of the 10,000 simulated games follows the exact same playstyle,
turn after turn. Understanding it helps you trust — and sanity-check — the
numbers, so here's the full picture of what the simulated player actually
does, in the order it does it.

### Building the opening hand (mulligans)

At the start of each game, the simulated player draws 7 cards and asks
itself one question: *"using only what's in this hand — no future draws —
could I cast at least two nonland cards within the first four turns?"* If
yes, it keeps the hand. If no, it mulligans: following the standard London
mulligan rule, it shuffles everything back in, draws a fresh 7, and will
need to set aside one card for each mulligan it's taken once it finally
keeps. It will try up to three times before being forced to keep whatever
it has.

When cards need to be set aside, it doesn't do so blindly: if the hand is
flooded with lands, it sets aside the extra lands first; otherwise, it sets
aside its most expensive cards, since those are the least likely to matter
in the immediate turns that decided whether the hand was worth keeping.

### Each turn, in order

1. **Draw a card.** Every turn, including turn 1 — this matches the actual
   tournament rule for multiplayer Commander specifically (the "skip your
   first draw" rule only applies to two-player games).

2. **Play one land.** This is the most consequential decision each turn, so
   it's worth walking through carefully. The simulated player looks at
   *everything* in hand that isn't castable yet with the mana currently
   available — creatures, instants, sorceries, mana rocks, mana dorks, ramp
   spells, all of it on equal footing — and identifies whichever one costs
   the least. Whatever color(s) that cheapest card needs, it tries to play a
   land that provides. If two or more different cards are tied for cheapest
   and need different colors, it looks for a land that covers as many of
   those needs as possible, preferring one that covers all of them if such a
   land exists in hand. Among lands that are equally good on color, it
   prefers ones that enter the battlefield untapped over ones that enter
   tapped; if a fetchland is available and nothing else distinguishes the
   choice, it treats cracking one as a small bonus, since it thins the deck
   for free. Fetchlands, and lands with entering-tapped conditions (Check
   Lands, Slow Lands, Fast Lands, and similar cycles), are resolved for
   real against the actual simulated library and battlefield state that
   game — not guessed at or approximated.

3. **Deploy one mana rock, mana dork, or ramp spell, if it can afford one —
   specifically, the *cheapest* one currently in hand that it can pay for.**
   This mirrors how these decks typically get sequenced in practice: a land
   plus one accelerant per turn. Mana rocks are usable the same turn they're
   played (matching the real rule that artifacts don't have summoning
   sickness); mana dorks come online starting the *following* turn (since
   creatures do have summoning sickness, unless the dork has Haste); a ramp
   spell's fetched land typically enters tapped, coming online the turn
   after that.

4. **Every other card in hand is left alone that turn** — no other spell
   actually gets cast. Instead, for every single nonland card in your whole
   decklist (whether or not it happens to be the one sitting in this
   particular hand this particular game), the tool checks whether that
   turn's mana could have paid for it, and records the result. This check is
   the actual measurement the entire tool is built around, and it's
   deliberately independent of the land-drop and accelerant decisions above
   — see ["What does this actually tell me?"](#what-does-this-actually-tell-me)
   near the top of this page for why that separation matters.

### What this playstyle is, and isn't

This is a consistent, mana-focused heuristic, not a strategic AI making
situational judgment calls. It doesn't hold back a removal spell for a
bigger threat, it doesn't bluff or play around anything, it has no idea what
your opponents are doing, and it will always play a land and deploy an
accelerant if it possibly can, every single turn. That's a deliberate
simplification, not an oversight: this tool exists to answer "does my mana
base support my costs," not "what's the objectively best play in this exact
moment." For that narrower, more useful question, always taking the
straightforward, mana-efficient line is the right assumption to build the
numbers on — a cleverer simulated player would introduce judgment calls that
have nothing to do with your manabase, muddying exactly the signal this tool
is trying to isolate.

## What This Tool Doesn't Do

This is a mana-base and curve calculator, not a full game simulator. Things
it deliberately does not model:

- **Life totals.** Fetch lands and shock lands are assumed to always pay
  their cost successfully — their life loss isn't tracked.
- **Opponents, combat, or board states.** It only knows about your deck's
  own cards and turn sequence.
- **Card abilities beyond "does this cost get paid."** It doesn't know what
  a spell *does* once cast, only whether the mana existed for it.
- **Every possible clever line of play.** It plays a reasonable, consistent
  strategy (one land and one mana rock/dork/ramp spell per turn, chasing
  whatever color your cheapest uncastable card needs) rather than searching
  for the objectively best possible play every turn.

None of these make the numbers wrong for what they're meant to answer — "can
my mana base support this card's cost" — but they're worth knowing about
before treating any single percentage as gospel. The full, much more
detailed list of modeling assumptions and known limitations is documented at
the top of `vibecodedCurveSim.py` itself, for anyone curious enough to read
the source.

## Contributing

Issues and pull requests are welcome. If you find a card or deck that
produces a result that looks wrong, please open an issue with the decklist
and the specific card/number you're questioning — that's exactly how most of
the bugs in this tool have been found so far.

## License

MIT — see [LICENSE](LICENSE). In short: free to use, modify, and redistribute,
including commercially, as long as the copyright notice stays attached.
