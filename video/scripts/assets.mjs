// Копирует экраны из design/screens и шрифт из webapp/fonts в video/public.
// Запускается перед dev и render (npm run assets): в git лежат только звук и снимки чата бота.
import { cpSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = (p) => fileURLToPath(new URL(`../../${p}`, import.meta.url));
const pub = (p) => fileURLToPath(new URL(`../public/${p}`, import.meta.url));

const SCREENS = [
  "teacher-call-d-n6-dark",
  "teacher-call-d-n30-dark",
  "student-call-m-l1-dark",
  "student-call-m-l2-dark",
  "student-call-m-l3-dark",
  "student-call-m-l4-dark",
  "student-call-m-ana-off-dark",
  "bot-summary-m-summary-dark",
  "bot-summary-m-card-dark",
];

mkdirSync(pub("screens"), { recursive: true });
mkdirSync(pub("fonts"), { recursive: true });
for (const name of SCREENS) cpSync(root(`design/screens/${name}.png`), pub(`screens/${name}.png`));
cpSync(root("webapp/fonts/Onest.ttf"), pub("fonts/Onest.ttf"));
console.log(`Скопировано экранов: ${SCREENS.length}, шрифт Onest`);
