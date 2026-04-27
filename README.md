# SmartBracelet

A wearable that lets you talk to AI without being at your computer.

## The Idea

You shouldn't have to be sitting at a desk to use AI tools. SmartBracelet is a tiny wrist-worn device that listens for voice commands, records what you say, and sends it to an AI agent that actually does stuff for you — runs commands, writes files, whatever you need. Results come back to your phone via Telegram.

Walk around. Be outside. Be cooking. Be anywhere. Touch Grass. Just talk to your wrist and things happen on your machine.


## How It Works

Currently it is just a button interaction.

### BUTTON MODE:
Press and hold button = record voice until you stop pressing
Press button once = snap image

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

