<!-- order: 20 -->
# VS Analysis

Measures the brightness of a variable star in the frames of your library sessions (aperture photometry) and produces an AAVSO report. Pick one or more sessions, check the parameters, run the photometry, then export the report.

## Refresh

Re-reads the image library for newly designated targets or sessions.

## Target star

The star to measure. It is filled in from the selected session; type an AAVSO name or AUID to override it.

## Filter band

The photometric band used for a session whose catalogued filter is blank. A session with a filter ignores this.

## Aperture radius (px)

The measuring aperture, in pixels. The sky annulus scales with it, from 1.5 to 2.5 times the radius.

## Run Photometry

Measures the target in every frame of the selected sessions and fills the results table.

## Stop

Stops after the frame being measured and keeps the results so far.

## Export AAVSO Report

Saves the results as an AAVSO Extended Format report ready to submit.
