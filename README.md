# SmartBracelet

A wearable that lets you talk to AI without being at your computer.

## The Idea

You shouldn't have to be sitting at a desk to use AI tools. SmartBracelet is a tiny wrist-worn device that listens for voice commands, records what you say, and sends it to an AI agent that actually does stuff for you — runs commands, writes files, whatever you need. Results come back to your phone via Telegram.

Walk around. Be outside. Be cooking. Be anywhere. Touch Grass. Just talk to your wrist and things happen on your machine.


## How It Works

Currently it is just a button interaction but there is technically also an implementation version with a tinyML model but it requires a LOT more training data to work. 

### BUTTON MODE:
Press and hold button = record voice until you stop pressing
Press button once = snap image

### VOICE MODE:

1. You wear a small ESP32 microcontroller on your wrist (camera + mic built in)
2. Say a wake word like "record" — it starts listening
3. Say what you need — *"commit my changes"*, *"take a note"*, whatever
4. Say "stop" — it sends your audio to a relay server on your local network
5. The server transcribes your speech and hands it to Claude Code
6. Claude Code does the thing
7. You get a Telegram message: done

You can also say "capture" to snap a photo (whiteboard, error on a screen, anything) and include that as context.

## Why

AI tools are powerful but they chain you to a keyboard and screen. This project breaks that chain. It's about **freedom** — accessing the same AI capabilities from anywhere in your space, hands-free, eyes-free.

## Tech

- **On your wrist:** XIAO ESP32S3 Sense (tiny board with mic + camera) running keyword detection via TinyML
- **On your machine:** Python relay server with Whisper (speech-to-text) + Claude Code (execution) 
- **On your phone:** Telegram bot for notifications

## Project Structure

```
firmware/    — code that runs on the bracelet (Arduino/PlatformIO)
relay/       — backend server that processes audio and runs AI (Python/FastAPI)
cad/         — 3D printable bracelet enclosure (STL files)
docs/        — extra documentation
```

## Status

Working prototype. Voice commands, photo capture, audio recording, AI execution, and Telegram feedback all functional. Actively iterating on the ML model accuracy and form factor.

## OLD Repo that needs cleaning up 

[github.com/FabriGu/smartBracelet](https://github.com/FabriGu/smartBracelet)

