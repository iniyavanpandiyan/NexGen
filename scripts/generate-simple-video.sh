#!/bin/bash
# Simple video generation script using ffmpeg
# This creates basic video frames that can be compiled into a video

OUTPUT_DIR="output"
FRAME_WIDTH=1080
FRAME_HEIGHT=1920
DURATION=60  # seconds
FPS=30

# Create output directory
mkdir -p "$OUTPUT_DIR"

echo "Generating simple test video..."
echo "Resolution: ${FRAME_WIDTH}x${FRAME_HEIGHT}"
echo "Duration: ${DURATION} seconds"
echo "Frame rate: ${FPS} fps"

# Generate frames using ffmpeg
ffmpeg -y -f lavfi -i color=c=black:s=${FRAME_WIDTH}x${FRAME_HEIGHT}:r=${FPS}-fps -t ${DURATION} \
  -vf "drawtext=text='CBSE Education':fontsize=72:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2, \
       drawtext=text='NCERT Textbook Summaries':fontsize=48:fontcolor=light_blue:x=(w-text_w)/2:y=(h-text_h)/2+50" \
  -frames:v $((DURATION * FPS)) \
  "$OUTPUT_DIR/frame_%04d.png"

echo "Generated frames in $OUTPUT_DIR/"
echo "Total frames: $(ls "$OUTPUT_DIR"/frame_*.png | wc -l)"

# Compile frames into video
ffmpeg -y -framerate ${FPS} -i "$OUTPUT_DIR"/frame_%04d.png -c:v libx264 -pix_fmt yuv420p \
  "output/test-video.mp4"

echo "Video created: output/test-video.mp4"
echo "Done!"
