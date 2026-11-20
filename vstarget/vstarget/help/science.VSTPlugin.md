<!-- order: 10 -->
# Variable Stars

Plans variable-star observations from the AAVSO Target Tool. The left side lists the targets you can choose from, the right side is your observation plan for the night. Download targets, narrow the list, add the ones you want to the plan, then turn the plan into a session on **Planning > Sessions**.

## Settings

Opens the plugin settings: your AAVSO API key and the telescope location used to decide which targets are visible.

## Download

Fetches targets from the AAVSO Target Tool for the ticked sections and replaces the list below. It needs the API key from **Settings**.

## All

Ticks every section.

## None

Clears every section tick.

## Alerts / Campaigns

Include the AAVSO alert notices and campaign targets in the download and in the list.

## Cataclysmic Variables

Include cataclysmic variables (novae, dwarf novae and similar).

## Eclipsing Variables

Include eclipsing binaries.

## Short-Period Pulsators

Include short-period pulsating stars.

## Long-Period Variables

Include long-period variables such as Miras.

## Young Stellar Objects

Include young stellar objects.

## High Energy Targets

Include X-ray and other high-energy sources.

## Exoplanets

Include exoplanet transit targets.

## Miscellaneous

Include targets that fit none of the other sections.

## Observable only (next night)

When ticked, a download returns only targets visible from your telescope location during the next night.

## Priority only

Hides every target that is not marked as a priority. This only filters the list already downloaded.

## Hide solar conj.

Hides targets that are close to the Sun, in solar conjunction, and so cannot be observed. This only filters the list already downloaded.

## Search

Filters the list as you type, by name, type or constellation.

## Add All Visible

Adds every target currently shown in the list, after search and the filters above, to the observation plan.

## Add Selected

Adds the selected rows to the plan. Double-clicking a row does the same.

## Move target up

Moves the selected target one place earlier in the plan, so it is observed sooner.

## Move target down

Moves the selected target one place later in the plan.

## Remove

Removes the selected targets from the plan.

## Clear All

Removes every target from the plan.

## Session Options

Tick the title to show the options for turning the plan into a session.

## Plate solve

Adds a plate solve after the slew to each target, so the pointing is corrected before imaging.

## Autofocus

Runs an autofocus on each target before imaging it.

## Dither

Dithers between each filter's exposures.

## Guiding

Starts guiding on each target and stops it when that target's exposures finish.

## Filter

The filter used for newly added targets. Several filters can be given, separated by commas.

## Count

How many exposures to take per filter for newly added targets.

## Interval (s)

The exposure length, in seconds, for newly added targets.

## Binning

The binning for newly added targets.

## Apply to All Targets in Plan

Overwrites the capture settings of every target already in the plan with the four defaults above.

## Suggest Exposures from Calibration

Sets each target's interval from its faint magnitude using your stored exposure calibration, limited so the comparison stars do not saturate.

## Preview Session

Shows the blocks the plan would create, without creating them.

## Create Session

Creates a session for the plan on **Planning > Sessions**, ready to run.
