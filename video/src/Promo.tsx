import React from 'react';
import { Audio, Sequence, interpolate, staticFile } from 'remotion';
import { Card, ClassInBot, Finale, Lesson, Problem, StudentView, SwitchOff, Summary } from './scenes';
import { SCENES, SPEED, TOTAL, starts } from './timeline';
import { useFont } from './ui';

const COMP = [Problem, ClassInBot, Lesson, StudentView, SwitchOff, Summary, Card, Finale];

// Звуки: Mixkit SFX Free License, источники в video/CREDITS.md.
const SFX: { at: number; file: string; vol: number }[] = [
  { at: 0, file: 'transition-soft.mp3', vol: 0.5 },
  { at: starts[1], file: 'swoosh-quick.mp3', vol: 0.4 },
  { at: starts[1] + 158, file: 'click-camera.mp3', vol: 0.35 },
  { at: starts[2], file: 'transition-soft.mp3', vol: 0.4 },
  { at: starts[3], file: 'transition-snap.mp3', vol: 0.35 },
  { at: starts[4], file: 'swoosh-quick.mp3', vol: 0.4 },
  { at: starts[4] + 85, file: 'click-camera.mp3', vol: 0.4 },
  { at: starts[5], file: 'transition-soft.mp3', vol: 0.4 },
  { at: starts[6], file: 'transition-snap.mp3', vol: 0.35 },
  { at: starts[7], file: 'impact-cine.mp3', vol: 0.55 },
];

export const Promo: React.FC<{ bgm?: boolean }> = ({ bgm = true }) => {
  useFont();
  return (
    <>
      {SCENES.map((s, i) => {
        const Scene = COMP[i];
        return (
          <Sequence key={s.id} from={starts[i]} durationInFrames={Math.round((s.sec * 30) / SPEED)}>
            <Scene len={s.sec * 30} />
          </Sequence>
        );
      })}
      {bgm && (
        <Audio
          src={staticFile('audio/bgm.mp3')}
          endAt={TOTAL}
          volume={(f) =>
            0.16 *
            interpolate(f, [0, 45, TOTAL - 75, TOTAL], [0, 1, 1, 0], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            })
          }
        />
      )}
      {SFX.map((x, i) => (
        <Sequence key={i} from={x.at}>
          <Audio src={staticFile(`audio/${x.file}`)} volume={x.vol} />
        </Sequence>
      ))}
    </>
  );
};
