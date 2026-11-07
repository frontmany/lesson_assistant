// Длительности кадров в секундах до ускорения (сумма 78 с); в ролике они делятся на SPEED, итого около 62 с.
export const SCENES = [
  { id: 'problem', sec: 9 },
  { id: 'class', sec: 11 },
  { id: 'lesson', sec: 12 },
  { id: 'student', sec: 10 },
  { id: 'switch', sec: 9 },
  { id: 'summary', sec: 10 },
  { id: 'card', sec: 8 },
  { id: 'finale', sec: 9 },
] as const;

export const SPEED = 1.25; // анимации идут в 1,25 раза быстрее

export const starts: number[] = [];
let acc = 0;
for (const s of SCENES) {
  starts.push(acc);
  acc += Math.round((s.sec * 30) / SPEED);
}
export const TOTAL = acc;
