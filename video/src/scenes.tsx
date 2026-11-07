import React from 'react';
import { AbsoluteFill, Img, interpolate, staticFile } from 'remotion';
import { C, FONT } from './theme';
import { SceneFade, Shot, TextCol, TopTitle, clamp, ease, nb, rise, useF } from './ui';
import { AnalyticsMenu, Row, TeacherWindow } from './Window';

const prog = (f: number, a: number, b: number, from: number, to: number) =>
  interpolate(f, [a, b], [from, to], { ...clamp, easing: ease });

const PH = 1624 / 750; // высота/ширина экранов телефона

/* 1. Проблема */
export const Problem: React.FC<{ len: number }> = ({ len }) => {
  const f = useF();
  const drift = prog(f, 0, len, 0, -30);
  return (
    <SceneFade len={len} bg={C.dark}>
      <TextCol
        dark
       
        title="Учитель не слышит каждого ученика"
        sub="Говорят одни и те же. Остальные молчат, и непонятно, кто что понял."
      />
      <div
        style={{
          position: 'absolute',
          left: 880,
          top: 230 + drift,
          opacity: 0.9 * rise(f, 10, 30),
          filter: 'grayscale(0.7) brightness(0.9)',
        }}
      >
        <div style={{ width: 960, height: 600, borderRadius: 16, overflow: 'hidden' }}>
          <Img src={staticFile('screens/teacher-call-d-n30-dark.png')} style={{ width: 960, height: 600, display: 'block' }} />
        </div>
      </div>
    </SceneFade>
  );
};

/* 2. Класс в боте */
export const ClassInBot: React.FC<{ len: number }> = ({ len }) => {
  const f = useF();
  const W = 480;
  const slot = { left: 840, top: 280 };
  const a3 = rise(f, 112, 18);
  const a2 = Math.min(rise(f, 12), 1 - a3);
  const a4 = rise(f, 205, 24);
  const chip = (text: string, o: number, left: number) => (
    <div
      style={{
        position: 'absolute',
        left,
        top: 214,
        padding: '8px 20px',
        borderRadius: 99,
        background: C.accentTint,
        color: C.accent,
        fontSize: 26,
        fontWeight: 600,
        opacity: o,
      }}
    >
      {text}
    </div>
  );
  return (
    <SceneFade len={len} bg={C.bg}>
      <TextCol title="Учитель создаёт класс" sub="Ученики входят по ссылке." />
      {chip('Учитель', Math.max(a2, a3), slot.left)}
      {chip('Ученик', a4, slot.left + W + 36)}
      <div style={{ position: 'absolute', ...slot, opacity: a2, transform: `translateY(${(1 - a2) * 30}px)` }}>
        <Shot src="reg-s2-dark.png" width={W} ratio={612 / 750} radius={28} />
      </div>
      <div style={{ position: 'absolute', ...slot, opacity: a3, transform: `translateY(${(1 - a3) * 20}px)` }}>
        <Shot src="reg-s3-dark.png" width={W} ratio={760 / 750} radius={28} />
      </div>
      <div
        style={{
          position: 'absolute',
          left: slot.left + W + 36,
          top: slot.top,
          opacity: a4,
          transform: `translateY(${(1 - a4) * 30}px)`,
        }}
      >
        <Shot src="reg-s4-dark.png" width={W} ratio={652 / 750} radius={28} />
      </div>
    </SceneFade>
  );
};

/* 3. Урок: шкалы растут */
export const Lesson: React.FC<{ len: number }> = ({ len }) => {
  const f = useF();
  const rows: Row[] = [
    { name: 'Артём К.', step: 1, tag: { text: 'давно не отвечал', warn: true, o: prog(f, 250, 275, 0, 1) } },
    { name: 'Алиса Н.', step: 1 + prog(f, 120, 200, 0, 1), tag: { text: 'тихий, но точный', o: prog(f, 215, 240, 0, 1) } },
    { name: 'Иван И.', step: 1 + prog(f, 40, 150, 0, 1) + prog(f, 160, 210, 0, 1) },
    { name: 'Мария С.', step: 1 + prog(f, 80, 170, 0, 1) },
    { name: 'Софья Л.', step: 1, off: true },
  ];
  const push = prog(f, 0, len, 1, 1.05);
  return (
    <SceneFade len={len} bg={C.bg}>
      <TopTitle title="Ассистент слышит каждого" sub="У учеников растут шкалы участия." />
      <div
        style={{
          position: 'absolute',
          left: 320,
          top: 225,
          transform: `scale(${push})`,
          transformOrigin: '70% 40%',
          opacity: rise(f, 6, 24),
        }}
      >
        <TeacherWindow rows={rows} />
      </div>
    </SceneFade>
  );
};

/* 4. Ученик видит только свою шкалу */
export const StudentView: React.FC<{ len: number }> = ({ len }) => {
  const f = useF();
  const shots = ['student-call-m-l1-dark.png', 'student-call-m-l2-dark.png', 'student-call-m-l3-dark.png', 'student-call-m-l4-dark.png'];
  const marks = [0, 70, 140, 215];
  const W = 400;
  const e = rise(f, 8, 30);
  return (
    <SceneFade len={len} bg={C.bg}>
      <TextCol
       
        title="Ученик видит только свою шкалу"
        sub="Её не видит никто другой. Шкала только растёт и не падает."
      />
      <div
        style={{
          position: 'absolute',
          left: 1200,
          top: 107,
          width: W,
          height: W * PH,
          opacity: e,
          transform: `translateY(${(1 - e) * 40}px)`,
        }}
      >
        {shots.map((s, i) => (
          <div key={s} style={{ position: 'absolute', inset: 0, opacity: i === 0 ? 1 : rise(f, marks[i], 18) }}>
            <Shot src={s} width={W} ratio={PH} radius={36} />
          </div>
        ))}
      </div>
    </SceneFade>
  );
};

/* 5. Учитель выключает аналитику одному ученику */
export const SwitchOff: React.FC<{ len: number }> = ({ len }) => {
  const f = useF();
  const menuO = Math.min(rise(f, 40, 16), 1 - rise(f, 150, 14));
  const on = 1 - prog(f, 100, 112, 0, 1);
  const off = f >= 150;
  const rows: Row[] = [
    { name: 'Артём К.', step: 1, tag: { text: 'давно не отвечал', warn: true, o: 1 } },
    { name: 'Алиса Н.', step: 2, tag: { text: 'тихий, но точный', o: 1 } },
    { name: 'Иван И.', step: 3 },
    { name: 'Мария С.', step: 2 },
    { name: 'Софья Л.', step: off ? 1 : 2, off },
  ];
  const ph = rise(f, 172, 24);
  const phoneH = 360 * PH * 0.9;
  return (
    <SceneFade len={len} bg={C.bg}>
      <TopTitle title="Учитель выключает аналитику одному ученику" sub="Он остаётся в уроке, но не попадает в шкалу." />
      <div style={{ position: 'absolute', left: 60, top: 220, opacity: rise(f, 6, 24) }}>
        <TeacherWindow rows={rows} scale={0.98}>
          <AnalyticsMenu name={nb("Софья Л.")} on={on} o={menuO} />
        </TeacherWindow>
      </div>
      <div style={{ position: 'absolute', left: 1440, top: 220, opacity: ph, transform: `translateY(${(1 - ph) * 30}px)` }}>
        <Shot src="student-call-m-ana-off-dark.png" width={360} ratio={PH} crop={0.9} radius={32} />
      </div>
      <div
        style={{
          position: 'absolute',
          left: 1440,
          width: 360,
          top: 220 + phoneH + 18,
          textAlign: 'center',
          fontSize: 28,
          color: C.inkSoft,
          opacity: ph,
          fontFamily: FONT,
        }}
      >
        Так видит ученик
      </div>
    </SceneFade>
  );
};

/* 6. Сводка учителю */
export const Summary: React.FC<{ len: number }> = ({ len }) => {
  const f = useF();
  const W = 560;
  const crop = 0.68;
  const wipe = prog(f, 40, 190, 0, 100);
  const o = rise(f, 8, 24);
  return (
    <SceneFade len={len} bg={C.bg}>
      <TextCol
       
        title="Бот присылает учителю сводку"
        sub="Кто вытянул урок, кто выпал, кого спросить в следующий раз."
      />
      <div
        style={{
          position: 'absolute',
          left: 1050,
          top: 540 - (W * PH * crop) / 2,
          opacity: o,
          transform: `translateY(${(1 - o) * 40}px)`,
        }}
      >
        <div style={{ clipPath: `inset(0 0 ${100 - wipe}% 0 round 36px)` }}>
          <Shot src="bot-summary-m-summary-dark.png" width={W} ratio={PH} crop={crop} radius={36} />
        </div>
      </div>
    </SceneFade>
  );
};

/* 7. Карточка ученика */
export const Card: React.FC<{ len: number }> = ({ len }) => {
  const f = useF();
  const W = 560;
  const crop = 0.44;
  const o = rise(f, 10, 26);
  return (
    <SceneFade len={len} bg={C.bg}>
      <TextCol title="Ученик получает личную карточку" sub="Что получилось и что повторить. Класс её не видит." />
      <div
        style={{
          position: 'absolute',
          left: 1050,
          top: 540 - (W * PH * crop) / 2,
          opacity: o,
          transform: `translateY(${(1 - o) * 40}px) scale(${0.96 + o * 0.04})`,
        }}
      >
        <Shot src="bot-summary-m-card-dark.png" width={W} ratio={PH} crop={crop} radius={36} />
      </div>
    </SceneFade>
  );
};

/* 8. Финал */
export const Finale: React.FC<{ len: number }> = ({ len }) => {
  const f = useF();
  const t = rise(f, 8, 34);
  const u = rise(f, 60, 30);
  const line = prog(f, 40, 100, 0, 1);
  return (
    <SceneFade len={len} bg={C.navy}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', textAlign: 'center' }}>
        <div
          style={{
            fontSize: 176,
            fontWeight: 800,
            color: '#fff',
            letterSpacing: '-0.03em',
            opacity: t,
            transform: `translateY(${(1 - t) * 30}px)`,
          }}
        >
          {nb('Урок в MAX')}
        </div>
        <div style={{ width: 360 * line, height: 6, borderRadius: 3, background: C.accentSoft, marginTop: 20 }} />
        <div style={{ marginTop: 44, fontSize: 44, color: '#c9d0dc', opacity: u }}>Ассистент слышит каждого ученика.</div>
        <div
          style={{
            marginTop: 56,
            padding: '16px 40px',
            borderRadius: 99,
            background: 'rgba(255,255,255,0.10)',
            fontSize: 42,
            color: '#fff',
            opacity: u,
          }}
        >
          github.com/frontmany/lesson_assistant
        </div>
      </AbsoluteFill>
    </SceneFade>
  );
};
