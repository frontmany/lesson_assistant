import React from 'react';
import { Img, staticFile } from 'remotion';
import { C, FONT } from './theme';

/**
 * Строка шкалы в панели «Класс» (как .stu в webapp/css/call.css):
 * имя, пометка рядом с именем, четыре сегмента и последняя реплика под ними.
 * step от 1 до 4, дробное значение — сегмент заполняется постепенно.
 */
export type Row = {
  name: string;
  step: number;
  tag?: { text: string; warn?: boolean; o: number };
  quote?: string;
  quoteO?: number;
  off?: boolean;
};

const Bars: React.FC<{ step: number }> = ({ step }) => (
  <div style={{ display: 'flex', gap: 4, marginTop: 9 }}>
    {[0, 1, 2, 3].map((i) => {
      const fill = Math.max(0, Math.min(1, step - i));
      return (
        <div key={i} style={{ flex: 1, height: 8, borderRadius: 4, background: C.muted, overflow: 'hidden' }}>
          <div style={{ width: `${fill * 100}%`, height: '100%', background: C.accent }} />
        </div>
      );
    })}
  </div>
);

/** Повтор правой панели «Класс» из актуального экрана: на ней шкалы растут. */
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
      boxSizing: 'border-box',
    }}
  >
    <div style={{ display: 'flex', alignItems: 'center', height: 54, padding: '0 16px 0 17px', gap: 8 }}>
      <span style={{ fontSize: 15, fontWeight: 700 }}>Класс</span>
      <span style={{ flex: 1, fontSize: 13, color: C.inkSoft }}>Учеников: {rows.length}</span>
      <span style={{ fontSize: 22, color: C.inkSoft, lineHeight: 1 }}>›</span>
    </div>
    <div style={{ padding: '0 16px' }}>
      {rows.map((r) => (
        <div key={r.name} style={{ padding: '15px 0', borderTop: `1px solid ${C.line}`, boxSizing: 'border-box' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 32 }}>
            <span style={{ fontSize: 15, fontWeight: 600, opacity: r.off ? 0.5 : 1, whiteSpace: 'nowrap' }}>{r.name}</span>
            {r.tag && !r.off && (
              <span
                style={{
                  fontSize: 13,
                  padding: '4px 10px',
                  borderRadius: 99,
                  whiteSpace: 'nowrap',
                  background: r.tag.warn ? C.warnTint : C.accentTint,
                  color: r.tag.warn ? C.warnInk : C.accent,
                  opacity: r.tag.o,
                }}
              >
                {r.tag.text}
              </span>
            )}
            <span style={{ flex: 1 }} />
            {r.off && <span style={{ fontSize: 13, color: C.inkSoft, opacity: 0.5, whiteSpace: 'nowrap' }}>аналитика выключена</span>}
            <span style={{ fontSize: 18, color: C.inkSoft, width: 24, textAlign: 'center' }}>⋮</span>
          </div>
          <div style={{ opacity: r.off ? 0.5 : 1 }}>
            <Bars step={r.step} />
          </div>
          {r.quote && (
            <div style={{ marginTop: 11, fontSize: 13, lineHeight: '18px', color: C.inkSoft, opacity: r.quoteO ?? 1 }}>
              {r.quote}
            </div>
          )}
        </div>
      ))}
    </div>
  </div>
);

/** Окно учителя 1280×800: актуальный экран и поверх него живая панель класса. */
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

/** Меню за троеточием: «Аналитика для ученика» с переключателем. on: 1 — включена, 0 — выключена. */
export const AnalyticsMenu: React.FC<{ on: number; o: number }> = ({ on, o }) => (
  <div
    style={{
      position: 'absolute',
      left: 935,
      top: 391,
      width: 279,
      padding: '12px 14px',
      boxSizing: 'border-box',
      background: C.surface,
      border: `1px solid ${C.line}`,
      borderRadius: 12,
      boxShadow: '0 10px 30px rgba(0,0,0,0.5)',
      fontFamily: FONT,
      opacity: o,
      transform: `translateY(${(1 - o) * 8}px)`,
      display: 'flex',
      alignItems: 'center',
      gap: 12,
    }}
  >
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: C.ink }}>Аналитика для ученика</div>
      <div style={{ fontSize: 13, lineHeight: '17px', color: C.inkSoft, marginTop: 2 }}>
        Когда выключена, ученик не попадает в шкалу
      </div>
    </div>
    <div
      style={{
        width: 40,
        height: 24,
        borderRadius: 12,
        flex: 'none',
        background: on > 0.5 ? C.accent : C.muted,
        position: 'relative',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: 3,
          left: 3 + on * 16,
          width: 18,
          height: 18,
          borderRadius: 9,
          background: on > 0.5 ? C.surface : C.inkSoft,
        }}
      />
    </div>
  </div>
);
