# AllSkyCam

Shows the newest image from an indi-allsky all-sky camera on your network, as a still picture and as an animation of the last few images. The page has three tabs: **Latest**, **Animation** and **Settings**. Galileo keeps fetching in the background, so the animation fills up while you work elsewhere.

## Fetch now

Downloads the newest image straight away instead of waiting for the next scheduled check. It does nothing while the plugin is not configured, or while a download is already running.

## Server address

The address of your indi-allsky web server, for example `http://allsky.local/indi-allsky`. Use the same address you type in a browser to reach the indi-allsky home page. If you leave off `http://` it is added for you. The server must let visitors see its images without logging in.

## Camera number

Which indi-allsky camera to show, for installations with more than one. **Default** shows indi-allsky's most recently used camera, which is right for a single-camera setup.

## Check every

How often, in seconds, Galileo asks indi-allsky for its newest image. There is no point checking more often than your camera takes exposures. If the newest image has not changed since the last check, nothing is downloaded.

## Images in animation

How many of the most recent images are kept for the animation. Older ones are dropped. Images are held in memory only, so the animation starts empty after Galileo restarts and fills as new images arrive. A very large number of full-size frames is capped at about 256 MB in total, oldest first.

## Pause during daytime

Stops fetching while the Sun is above the **Sun altitude limit**, so the animation does not fill with overexposed daytime frames. The Sun's position is worked out from the latitude and longitude of the Observatory selected at the top of Galileo; if that Observatory has no location set, the option has no effect and a note appears in the status line.

## Sun altitude limit

The daytime pause applies while the Sun is higher than this many degrees above the horizon. `0°` is sunrise and sunset; a negative value such as `-6°` keeps fetching a little into twilight. Only used when **Pause during daytime** is on.

## Animation speed

How many images per second the animation plays, in the **Animation** tab and the animation pane.

## Show animation pane

Opens the animation in a pane docked to the Galileo window, so it stays visible on any screen. Drag the pane by its title to move it to another edge or float it; its close button turns this option off. The choice is remembered.

## Test connection

Tries the address and camera number currently in the form, without saving them, and reports whether an image came back or what went wrong. A web page coming back instead of an image usually means the address is not the indi-allsky root, or that indi-allsky requires a login.

## Save

Applies and stores all of the options on this tab. Changing the server address or camera number starts the animation over.
