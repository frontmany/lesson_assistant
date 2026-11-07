import React from 'react';
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  continueRender,
  delayRender,
  Easing,
} from 'remotion';
import { C, FONT } from './theme';

/** Неразрывные пробелы после коротких слов и перед инициалом: строка не рвёт «в уроке», «Софья Л.». */
export const nb = (t: string) =>
  t
    .replace(/(^|\s)(в|к|с|о|у|и|а|я|на|не|по|за|из|от|до|но|же|те)\s+/gi, '$1$2 ')
    .replace(/\s([А-ЯЁ]\.)/g, ' $1');

import { SPEED } from './timeline';

/** Время кадра с ускорением: все анимации считаем по нему. */
export const useT = () => useCurrentFrame() * SPEED;

export const useF = useT;

export const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;
export const ease = Easing.bezier(0.22, 1, 0.36, 1);

/** Кривая появления: 0 до `from`, потом плавно к 1 за `len` кадров. */
export const rise = (frame: number, from: number, len = 22) =>
  interpolate(frame, [from, from + len], [0, 1], { ...clamp, easing: ease });

export const useFont = () => {
  const [handle] = React.useState(() => delayRender('font'));
  React.useEffect(() => {
    const f = new FontFace('Onest', `url(${staticFile('fonts/Onest.ttf')})`, { weight: '100 900' });
    f.load()
      .then(() => {
        document.fonts.add(f);
        continueRender(handle);
      })
      .catch(() => continueRender(handle));
  }, [handle]);
};

/** Обёртка кадра: плавное появление и уход, в конце кадра свет не гаснет резко. */
export const SceneFade: React.FC<{ len: number; bg: string; children: React.ReactNode }> = ({
  len,
  bg,
  children,
}) => {
  const f = useCurrentFrame();
  const end = len / SPEED;
  const o = Math.min(
    interpolate(f, [0, 10], [0, 1], clamp),
    interpolate(f, [end - 10, end], [1, 0], clamp),
  );
  return (
    <AbsoluteFill style={{ background: bg, fontFamily: FONT }}>
      <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>
    </AbsoluteFill>
  );
};

/** Текст слева: заголовок и пояснение. Титры только на русском. */
export const TextCol: React.FC<{
  title: string;
  sub: string;
  dark?: boolean;
  kicker?: string;
}> = ({ title, sub, dark, kicker }) => {
  const f = useT();
  const t = rise(f, 6, 26);
  const s = rise(f, 26, 26);
  const ink = dark ? C.onDark : C.ink;
  return (
    <div
      style={{
        position: 'absolute',
        left: 120,
        top: 0,
        bottom: 0,
        width: 700,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
      }}
    >
      {kicker && (
        <div
          style={{
            fontSize: 28,
            fontWeight: 600,
            color: dark ? C.accentSoft : C.accent,
            marginBottom: 24,
            opacity: t,
          }}
        >
          {nb(kicker)}
        </div>
      )}
      <div
        style={{
          fontSize: 78,
          lineHeight: 1.08,
          fontWeight: 700,
          color: ink,
          letterSpacing: '-0.015em',
          opacity: t,
          transform: `translateY(${(1 - t) * 24}px)`,
        }}
      >
        {nb(title)}
      </div>
      <div
        style={{
          marginTop: 34,
          fontSize: 38,
          lineHeight: 1.3,
          color: C.inkSoft,
          opacity: s,
          transform: `translateY(${(1 - s) * 18}px)`,
        }}
      >
        {nb(sub)}
      </div>
    </div>
  );
};

/** Заголовок сверху по центру, для кадров с широким окном. */
export const TopTitle: React.FC<{ title: string; sub: string }> = ({ title, sub }) => {
  const f = useT();
  const t = rise(f, 6, 26);
  const s = rise(f, 24, 26);
  return (
    <div style={{ position: 'absolute', top: 54, left: 0, right: 0, textAlign: 'center' }}>
      <div
        style={{
          fontSize: 66,
          fontWeight: 700,
          color: C.ink,
          letterSpacing: '-0.015em',
          opacity: t,
          transform: `translateY(${(1 - t) * 20}px)`,
        }}
      >
        {nb(title)}
      </div>
      <div style={{ marginTop: 12, fontSize: 34, color: C.inkSoft, opacity: s }}>{nb(sub)}</div>
    </div>
  );
};

/** Экран телефона: скругление, тень, обрезка по высоте (ratio = высота/ширина картинки). */
export const Shot: React.FC<{
  src: string;
  width: number;
  ratio: number;
  /** показать только верхнюю часть картинки, доля высоты 0..1 */
  crop?: number;
  radius?: number;
  style?: React.CSSProperties;
}> = ({ src, width, ratio, crop = 1, radius = 36, style }) => (
  <div
    style={{
      width,
      height: width * ratio * crop,
      overflow: 'hidden',
      borderRadius: radius,
      boxShadow: '0 30px 70px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.10)',
      background: C.surface,
      ...style,
    }}
  >
    <Img src={staticFile(`screens/${src}`)} style={{ width, display: 'block' }} />
  </div>
);

/** Простой эффект «пружина» для карточек. */
export const useSpring = (delay: number, damping = 18) => {
  const f = useT();
  const { fps } = useVideoConfig();
  return spring({ frame: f - delay, fps, config: { damping, stiffness: 110 } });
};
