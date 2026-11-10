# ProScan Client for Home Assistant

Watch and listen to a scanner that's running through [ProScan](https://www.proscan.org)'s built-in web server.

- **Display camera:** the scanner's live LCD as a camera entity, the same image ProScan's virtual display shows in a browser.
- **Audio stream:** the scanner audio as a media source, so you can play it on any media player that handles MP3 streams (browser, ESPHome speakers, Music Assistant, Sonos, and so on).

The integration doesn't create sensors for the channel, talkgroup, frequency or anything else being received. ProScan sends the display only as images, and nothing from the scanner is written to Home Assistant's state or history. The recorder only sees the camera's idle/streaming state.

## Requirements

- ProScan with **Web Server** enabled (HTTP or HTTPS). Tested with ProScan 24.15 and a Uniden SDS-200.
- Home Assistant 2025.1 or newer.

## Installation

1. In HACS, go to **⋮ → Custom repositories** and add `https://github.com/RIGeek/HACS-ProScan-Client` as an **Integration**.
2. Install **ProScan Client** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → ProScan Client**.

| Field | What to enter |
|---|---|
| Server URL | The address you open in a browser, for example `https://scanner.example.net` or `http://192.168.1.20:5000` |
| Stream name | ProScan's **Stream Name** setting (`radio` if you didn't change it) |
| Stream password | Only if ProScan's *Serve Audio Stream* password is set |
| Monitor password | Only if ProScan's *Serve Scanner Monitor Mode* password is set. The display needs it. |
| Verify SSL | Turn off only for a self-signed HTTPS certificate |

**Options** (⚙ on the integration): stream name, passwords, and the **display idle timeout**.

## Using it

### Display

The camera is `camera.<name>_display`. A picture card with live view shows the LCD as it updates (about 3 frames per second):

```yaml
type: picture-entity
entity: camera.sds200_display
camera_view: live
show_name: false
show_state: false
```

The integration opens ProScan's display connection only while something is viewing the camera, and closes it after the idle timeout (60 s by default). An unwatched dashboard costs the server nothing.

### Audio

Open **Media** in the sidebar, then **ProScan**, pick the scanner, and choose a player. From an automation or script:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.barn_speakers
data:
  media_content_id: media-source://proscan/<config entry id>
  media_content_type: audio/mpeg
```

The easiest way to get the exact `media_content_id` is to play the stream once from the media browser and copy it from the player's attributes.

> **Mixed content:** if Home Assistant is opened over HTTPS and ProScan is only on HTTP, browsers block the stream when it plays *in the browser*. Speakers and other network players are unaffected. Use ProScan's HTTPS server to fix it.

## How it works

ProScan's web page uses an undocumented WebSocket at the server root:

- After sending `M,<id>,panel;` and `A,D`, the server sends `D,<base64 PNG>` display frames.
- A repeated frame is sent as `B,<checksum>`, where the checksum is the sum of the base64 string's character codes.
- Login is a POST to `/` with the form data in an `x-custom-header` header.

The audio is ProScan's normal MP3 stream at `/<stream name>`, with `+<password>` appended when a password is set.

## License

MIT
