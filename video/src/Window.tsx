import React from 'react';
import { Img, staticFile } from 'remotion';
import { C, FONT } from './theme';

/** Строка шкалы участия учителя: step от 1 до 4, дробное значение — полоска растёт. */
export type Row = {
  name: string;
  step: number;
  tag?: { text: string; warn?: boolean; o: number };
  off?: boolean;
};

const LEVELS = ['Пока тихо', 'Есть ответ', 'Участвует', 'Активно'];
const TOPS = [97, 201, 305, 376, 447];
const HEIGHTS = [104, 104, 71, 71, 71];

const Bars: React.FC<{ step: number; off?: boolean }> = ({ step, off }) => (
  <div style={{ display: 'flex', gap: 5, position: 'absolute', left: 17, right: 17 }}>
    {[0, 1, 2, 3].map((i) => {
      const fill = Math.max(0, Math.min(1, step - i));
      return (
        <div key={i} style={{ flex: 1, height: 7, borderRadius: 4, background: C.muted, overflow: 'hidden' }}>
          <div style={{ width: `${fill * 100}%`, height: '100%', background: off ? C.accentSoft : C.accent }} />
        </div>
      );
    })}
  </div>
);

/** Повтор правой панели «Класс» из макета teacher-call: на ней шкалы растут. */
export const ClassPanel: React.FC<{ rows: Row[] }> = ({ rows }) => (
  <div
    style={{
      position: 'absolute',
      left: 904,
      top: 16,
      width: 360,
      height: 694,
      background: C.surface,
      border: `1px solid ${C.line}`,
      borderRadius: 14,
      fontFamily: FONT,
      color: C.ink,
    }}
  >
    <div style={{ position: 'absolute', left: 17, top: 22, fontSize: 15, fontWeight: 700 }}>
      Класс <span style={{ fontSize: 12, fontWeight: 400, color: C.inkSoft, marginLeft: 6 }}>Учеников: 5</span>
    </div>
    <div
      style={{ position: 'absolute', left: 17, top: 56, right: 17, fontSize: 12, lineHeight: '16px', color: C.inkSoft }}
    >
      Шкала участия: пока тихо · есть ответ · участвует · активно
    </div>
    {rows.map((r, i) => {
      const level = r.off ? 'аналитика выключена' : LEVELS[Math.min(3, Math.floor(r.step + 0.001) - 1)];
      return (
        <div
          key={r.name}
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: TOPS[i],
            height: HEIGHTS[i],
            borderTop: `1px solid ${C.line}`,
          }}
        >
          <div
            style={{ position: 'absolute', left: 17, top: 17, fontSize: 14.5, fontWeight: 600, color: r.off ? '#97a0ae' : C.ink }}
          >
            {r.name}
          </div>
          <div style={{ position: 'absolute', right: 40, top: 19, fontSize: 12, color: r.off ? '#97a0ae' : C.inkSoft }}>
            {level}
          </div>
          <div style={{ position: 'absolute', right: 17, top: 14, fontSize: 18, color: C.inkSoft }}>⋮</div>
          <div style={{ position: 'absolute', left: 0, right: 0, top: 52 }}>
            <Bars step={r.step} off={r.off} />
          </div>
          {r.tag && (
            <div
              style={{
                position: 'absolute',
                left: 17,
                top: 70,
                fontSize: 12,
                padding: '4px 10px',
                borderRadius: 99,
                background: r.tag.warn ? C.warnTint : C.accentTint,
                color: r.tag.warn ? C.warnInk : C.accent,
                opacity: r.tag.o,
              }}
            >
              {r.tag.text}
            </div>
          )}
        </div>
      );
    })}
  </div>
);

/** Окно учителя 1280×800: картинка макета и поверх неё живая панель класса. */
export const TeacherWindow: React.FC<{
  rows: Row[];
  scale?: number;
  children?: React.ReactNode;
}> = ({ rows, scale = 1, children }) => (
  <div
    style={{
      width: 1280 * scale,
      height: 800 * scale,
      borderRadius: 18 * scale,
      overflow: 'hidden',
      position: 'relative',
      boxShadow: '0 40px 90px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.10)',
    }}
  >
    <div
      style={{
        width: 1280,
        height: 800,
        transform: `scale(${scale})`,
        transformOrigin: '0 0',
        position: 'absolute',
        left: 0,
        top: 0,
      }}
    >
      <Img src={staticFile('screens/teacher-call-d-n6-dark.png')} style={{ width: 1280, height: 800, display: 'block' }} />
      <ClassPanel rows={rows} />
      {children}
    </div>
  </div>
);

/** Меню «Аналитика для ученика» с переключателем. on: 1 — включена, 0 — выключена. */
export const AnalyticsMenu: React.FC<{ name: string; on: number; o: number }> = ({ name, on, o }) => (
  <div
    style={{
      position: 'absolute',
      left: 967,
      top: 372,
      width: 280,
      padding: '14px 15px',
      background: C.surface,
      border: `1px solid ${C.line}`,
      borderRadius: 12,
      boxShadow: '0 10px 30px rgba(0,0,0,0.5)',
      fontFamily: FONT,
      opacity: o,
      transform: `translateY(${(1 - o) * 8}px)`,
    }}
  >
    <div style={{ fontSize: 12, color: C.inkSoft }}>{name}</div>
    <div style={{ display: 'flex', alignItems: 'center', marginTop: 10 }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: C.ink }}>Аналитика для ученика</div>
        <div style={{ fontSize: 11.5, lineHeight: '15px', color: C.inkSoft, marginTop: 3 }}>
          Когда выключена, ученик не попадает в шкалу
        </div>
      </div>
      <div
        style={{
          width: 40,
          height: 24,
          borderRadius: 12,
          marginLeft: 12,
          background: on > 0.5 ? C.accent : '#b9b4a6',
          position: 'relative',
        }}
      >
        <div
          style={{ position: 'absolute', top: 3, left: 3 + on * 16, width: 18, height: 18, borderRadius: 9, background: '#fff' }}
        />
      </div>
    </div>
  </div>
);
