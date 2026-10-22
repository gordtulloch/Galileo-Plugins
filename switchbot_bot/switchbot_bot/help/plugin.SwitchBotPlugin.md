# SwitchBot

Controls SwitchBot Bot devices over Bluetooth Low Energy — for example a Bot stuck over a Seestar's power button so Galileo can switch it on and off. It appears on **Equipment > Switches**. Your computer needs Bluetooth and the `bleak` Python package (`pip install bleak`).

## Scan for SwitchBots

Listens for about five seconds for SwitchBot Bots in range and adds each one found as a box below. A sleeping Bot may not be heard; press its button once to wake it, then scan again. Bots already listed are not added twice.

## Add by address…

Adds a Bot by hand when the scan cannot find it. **Address** is the Bluetooth address (on macOS, the long identifier shown by the scan). **Label** is the name shown in Galileo and used for the switch in sessions, for example "Seestar power". **Password** is only needed if you set one for the Bot in the SwitchBot app; leave it empty otherwise. The password is stored unencrypted in Galileo's settings, so use a throwaway one.

## On

In the Bot's **dual-state mode** this extends the arm (switch on). In **press mode** it presses the button once. The status line shows which mode the Bot is in.

## Off

In dual-state mode this retracts the arm (switch off). In press mode it presses the button once, so a Seestar needs one press to start and one to stop.

## Press

Presses once whatever the Bot's mode.

## Check

Reads the Bot's battery level and mode. Galileo does this once when the box appears, but does not poll: every contact wakes the Bot and wears its battery. A Bot cannot report which way it was last switched, so the status shows the last command Galileo sent.

## Remove

Forgets the Bot. Scan again to find it.
