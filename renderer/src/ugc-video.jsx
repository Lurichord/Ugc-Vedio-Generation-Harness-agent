import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
} from 'remotion';

const clamp = {
  extrapolateLeft: 'clamp',
  extrapolateRight: 'clamp',
};

const ClipMedia = ({clip}) => {
  const frame = useCurrentFrame();
  const progress = interpolate(
    frame,
    [0, Math.max(1, clip.duration_in_frames - 1)],
    [0, 1],
    clamp,
  );
  const scale = clip.motion_preset === 'native_video' ||
    clip.motion_preset === 'ai_screen_motion'
    ? 1
    : interpolate(
      progress,
      [0, 1],
      [clip.scale_start, clip.scale_end],
      clamp,
    );
  const translateX = clip.motion_preset === 'slow_pan'
    ? interpolate(progress, [0, 1], [-24, 24], clamp)
    : 0;
  const punch = clip.transition_in === 'punch_cut'
    ? interpolate(frame, [0, 4], [1.045, 1], clamp)
    : 1;
  const style = {
    width: '100%',
    height: '100%',
    objectFit: clip.fit_mode,
    transform: `translateX(${translateX}px) scale(${scale * punch})`,
  };

  if (clip.media_type === 'video') {
    return (
      <OffthreadVideo
        src={staticFile(clip.media_path)}
        style={style}
        muted
        loop
        pauseWhenBuffering
      />
    );
  }
  return <Img src={staticFile(clip.media_path)} style={style} />;
};

const Caption = ({cue}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 3], [0, 1], clamp);
  const scale = interpolate(frame, [0, 4], [0.97, 1], clamp);
  return (
    <AbsoluteFill
      style={{
        justifyContent: 'flex-end',
        alignItems: 'center',
        paddingBottom: 210,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          maxWidth: 920,
          padding: '18px 30px 20px',
          borderRadius: 24,
          background: 'rgba(0,0,0,0.52)',
          color: '#fff',
          fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
          fontSize: 62,
          fontWeight: 800,
          lineHeight: 1.28,
          letterSpacing: 1,
          textAlign: 'center',
          textShadow: '0 3px 8px rgba(0,0,0,0.9)',
          opacity,
          transform: `scale(${scale})`,
        }}
      >
        {cue.text}
      </div>
    </AbsoluteFill>
  );
};

const Overlay = ({item}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    frame,
    [0, 4, Math.max(5, item.duration_in_frames - 5), item.duration_in_frames],
    [0, 1, 1, 0],
    clamp,
  );
  const positions = {
    top_left: {top: 72, left: 56},
    top_right: {top: 72, right: 56},
    bottom_left: {bottom: 72, left: 56},
  };
  const palette = {
    source_attribution: ['rgba(0,0,0,0.72)', '#fff'],
    generated_media_disclosure: ['rgba(255,188,45,0.92)', '#16120a'],
    interpretation_label: ['rgba(66,133,244,0.92)', '#fff'],
  };
  const [background, color] = palette[item.overlay_type] ??
    ['rgba(0,0,0,0.72)', '#fff'];
  return (
    <div
      style={{
        position: 'absolute',
        ...positions[item.position],
        zIndex: 20,
        maxWidth: 720,
        padding: '12px 20px',
        borderRadius: 14,
        background,
        color,
        fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
        fontSize: 30,
        fontWeight: 700,
        lineHeight: 1.25,
        opacity,
      }}
    >
      {item.text}
    </div>
  );
};

export const UGCVideo = (props) => {
  return (
    <AbsoluteFill style={{backgroundColor: '#05070a'}}>
      <Audio src={staticFile(props.audio_path)} />
      {props.clips.map((clip) => (
        <Sequence
          key={clip.clip_id}
          from={clip.start_frame}
          durationInFrames={clip.duration_in_frames}
          premountFor={15}
        >
          <AbsoluteFill style={{overflow: 'hidden'}}>
            <ClipMedia clip={clip} />
          </AbsoluteFill>
        </Sequence>
      ))}
      {props.overlays.map((item) => (
        <Sequence
          key={item.overlay_id}
          from={item.start_frame}
          durationInFrames={item.duration_in_frames}
        >
          <Overlay item={item} />
        </Sequence>
      ))}
      {props.captions.map((cue) => (
        <Sequence
          key={cue.cue_id}
          from={cue.start_frame}
          durationInFrames={cue.duration_in_frames}
        >
          <Caption cue={cue} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
