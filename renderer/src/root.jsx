import React from 'react';
import {Composition} from 'remotion';
import {UGCVideo} from './ugc-video.jsx';

const defaultProps = {
  duration_in_frames: 1,
  fps: 30,
  clips: [],
  captions: [],
  overlays: [],
  audio_path: '',
};

export const RemotionRoot = () => {
  return (
    <Composition
      id="UGCFinal"
      component={UGCVideo}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={1}
      defaultProps={defaultProps}
      calculateMetadata={({props}) => ({
        durationInFrames: props.duration_in_frames,
        fps: props.fps,
        width: 1080,
        height: 1920,
      })}
    />
  );
};
