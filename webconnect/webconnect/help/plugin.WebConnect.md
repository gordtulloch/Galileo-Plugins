# WebConnect

Lets you watch and run this observatory from a web browser on your own network: a tablet at the telescope, a laptop in the house. Open the address shown under **Address to open**, ask to connect, and approve the request here. The Galileo computer decides everything: a browser can only ask. Connect Link must be switched on under **Options > Connect Link**.

WebConnect belongs on the Master computer. On a computer that is a NodeConnect spoke it stays switched off.

## Web application

The part of WebConnect that browsers talk to. It runs separately from Galileo, so a problem in it cannot stop an exposure or a session.

## Address to open

Type this into the browser. The first time, the browser will warn that the certificate is not trusted, because Galileo made it itself; check that the address is yours, then continue. Empty while the web application is stopped.

## Start web application

Starts the web application. It also starts by itself with Galileo unless **Start with Galileo** is off.

## Stop web application

Stops the web application. Browsers can no longer connect, and nothing is left listening. Approved browsers are remembered.

## Port

The network port the web application listens on. If you change it, the web application restarts when you press **Save**.

## Start with Galileo

Starts the web application whenever Galileo starts.

## Allow all network interfaces

Normally the web application listens only on this computer's own network address. Turn this on to listen on every network connection this computer has. Leave it off unless you know you need it.

## Sign out after idle (minutes)

How long a browser may sit unused before it has to sign in again. Signing in again is one click; the browser stays approved.

## Save

Keeps these settings. Nothing changes until you press it.

## Browsers

Who may connect. A new browser is a **Viewer**: it can see but not operate. Promote it to **Operator** here, never from the browser itself.

## Pairing requests

Browsers asking to connect, each with a code. The same code is shown on the browser. Approve only if they match and you recognise the device.

## Approve

Lets the selected browser connect, as a Viewer.

## Reject

Turns the selected request down.

## Approved browsers

Browsers that have been approved, with their role and when they were last seen.

## Make Operator

Lets the selected browser run the observatory: slew, take exposures, start sessions. It still needs the control lease for a Pier, as the desktop does, and the stop, park and close-dome buttons work without it.

## Make Viewer

Takes the selected browser back to watching only. Any control it holds is released.

## Revoke

Cuts the selected browser off at once, releases anything it holds and ends its sessions. It must be approved again to return.

## Remove browser

Revokes the selected browser and forgets it.

## Expire after (days)

How many days the selected browser stays approved. **Never** means it stays approved until you revoke it.

## Set expiry

Applies **Expire after (days)** to the selected browser.

## Recent activity

What browsers have asked for lately, including requests that were refused.

## Requests from browsers

The newest first: when, which browser, what it asked, and whether it was allowed.
