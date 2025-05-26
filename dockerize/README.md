# WebRTC Audio Streaming Application

This application allows real-time audio streaming between hosts and participants using WebRTC.

## Prerequisites

### System Requirements

1. Docker and Docker Compose
2. PulseAudio (for audio streaming)
3. Working microphone and speakers/headphones

### Installation Instructions

#### Ubuntu/Debian

```bash
# Install Docker
sudo apt-get update
sudo apt-get install -y docker.io docker-compose

# Install PulseAudio and required audio utilities
sudo apt-get install -y \
    pulseaudio \
    pulseaudio-utils \
    alsa-utils \
    pavucontrol

# Start PulseAudio if not running
pulseaudio --start

# Add your user to the audio and docker groups
sudo usermod -aG audio $USER
sudo usermod -aG docker $USER
```

#### Fedora/RHEL

```bash
# Install Docker
sudo dnf install -y docker docker-compose

# Install PulseAudio and required audio utilities
sudo dnf install -y \
    pulseaudio \
    pulseaudio-utils \
    alsa-utils \
    pavucontrol

# Start PulseAudio if not running
pulseaudio --start

# Add your user to the audio and docker groups
sudo usermod -aG audio $USER
sudo usermod -aG docker $USER
```

#### Arch Linux

```bash
# Install Docker
sudo pacman -S docker docker-compose

# Install PulseAudio and required audio utilities
sudo pacman -S \
    pulseaudio \
    pulseaudio-utils \
    alsa-utils \
    pavucontrol

# Start PulseAudio if not running
pulseaudio --start

# Add your user to the audio and docker groups
sudo usermod -aG audio $USER
sudo usermod -aG docker $USER
```

### Verifying Installation

1. Check PulseAudio is running:

```bash
pulseaudio --check
```

2. Verify audio devices:

```bash
# List audio devices
pactl list short sources
pactl list short sinks
```

3. Test Docker installation:

```bash
docker --version
docker-compose --version
```

## Running the Application

1. Clone the repository and navigate to the dockerize directory:

```bash
cd dockerize
```

2. Create a .env file with your desired port configuration:

```bash
echo "PORT=8000" > .env
```

3. Build and start the container:

```bash
# If running with sudo
sudo -E docker-compose up --build

# If user is in docker group
docker-compose up --build
```

4. Access the application at: `http://localhost:8000`

## Troubleshooting

### Common Issues

1. **PulseAudio Connection Refused**

   ```bash
   # Restart PulseAudio
   pulseaudio -k
   pulseaudio --start
   ```
2. **Audio Device Not Found**

   ```bash
   # Check available devices
   pactl list sources
   pactl list sinks

   # Use pavucontrol to configure audio devices
   pavucontrol
   ```
3. **Permission Issues**

   ```bash
   # Verify group memberships
   groups $USER

   # If groups were just added, log out and back in
   # or restart your system
   ```

### Environment Variables

The application uses the following environment variables:

- `PORT`: The port on which the application runs (default: 8000)
- `XDG_RUNTIME_DIR`: Set by your system, needed for PulseAudio socket
- `HOME`: Your home directory path

## Notes

- Make sure PulseAudio is running on your host system
- The application needs access to your system's audio devices
- If using sudo to run docker-compose, remember to use `sudo -E` to preserve environment variables
