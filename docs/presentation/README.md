# Презентация второго этапа

- `stage2.pdf` — готовая презентация для команды.
- `stage2.html` — исходник. Каждый слайд — `<section class="slide">` размером 1280×720.

## Как пересобрать PDF

Шрифты и иконки грузятся из интернета, поэтому страницу лучше открывать через локальный сервер:

```bash
python -m http.server 8765 --directory docs/presentation
```

Затем в Chrome откройте http://localhost:8765/stage2.html → «Печать» → «Сохранить как PDF», поля «Нет», фон включён.

Один слайд отдельно: http://localhost:8765/stage2.html?slide=5
