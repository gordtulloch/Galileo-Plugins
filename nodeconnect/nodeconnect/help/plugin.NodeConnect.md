# NodeConnect

Keeps this computer's equipment and settings in step with a **Master** computer over Connect Link. Typical use: set everything up once on the observatory computer (the Master), then pair a laptop or a Raspberry Pi (a spoke) to it. A spoke never changes what it is doing mid-session: changes it receives wait until no session is running. Connect Link must be switched on under **Options > Connect Link** on the Master.

A computer is either a spoke or a Master, never both.

## This node's Master

The spoke's half of the page. Enter the Master's address, press **Pair with Master**, compare the code shown here with the one on the Master, and approve it there.

## Master address

The address of the Master's Connect Link, for example `https://observatory.local:8765`. If you leave off `https://` it is added for you. Disabled once this computer is paired, and on a computer that already has spokes of its own.

## Pair with Master

Asks the Master to pair with this computer. A confirmation code appears; the same code must appear on the Master, where a person approves the request. The Master's certificate is checked and remembered, so later connections can only reach that same Master.

## Unpair

Forgets the Master and its credentials. This computer keeps its current settings and becomes standalone. Also cancels a pairing request that is still waiting.

## Sync now

Runs a sync straight away instead of waiting for the next scheduled one. Enabled once paired.

## Sync every

How often, in seconds, this spoke asks the Master for changes and sends its own. The minimum is 30 seconds. A failed sync is retried after a short, growing delay.

## Limit image transfer to a time window

Sends images to the Master only between **Transfer from** and **Transfer to**, for example overnight when the network is quiet. Settings are always synced regardless of this window. Images queue up outside the window and are sent when it opens.

## Transfer from

The hour (on this computer's clock) when image transfer may begin. A window such as 22:00 to 06:00 may cross midnight.

## Transfer to

The hour when image transfer stops. Equal hours mean no limit.

## Remove local images once the Master has them

Off by default. When on, an image is deleted from this computer only after the Master has received it and its checksum has been verified; it stays listed in the Library marked as on the Master, and can be retrieved later. Images are never removed while a session is running.

## Nodes paired with this one

The Master's half of the page, not used on a spoke.

## Pairing requests

Computers waiting to pair, each with its confirmation code. Compare the code with the one on the other computer before approving.

## Approve

Pairs the selected request. The node gets permission to read and write configuration and to send and receive data, and nothing else.

## Reject

Turns down the selected pairing request.

## Paired nodes

Computers paired with this one, with when each last made contact.

## Revoke

Stops the selected node from syncing. It can be paired again later.

## Remove node

Forgets the selected node completely, including what it had already received.
