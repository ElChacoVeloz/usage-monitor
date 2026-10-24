# AI Usage Monitor

Tablero de cuotas de las suscripciones de modelos de IA, pensado para una **tablet
vieja en kiosco** (nació en una Nexus 7 2013 con Android 6; hoy corre en un iPad
Mini). Lienzo de **1920 de ancho con alto adaptativo**: en 16:10 da 1920x1200 y en
4:3 da 1920x1440, escalado a la pantalla. Tema oscuro, **sin dependencias y sin
build**: Python estándar en el servidor y HTML/CSS/JS a mano en el cliente.

![Tablero con datos de ejemplo](docs/demo.png)

```
python3 server.py                 # http://0.0.0.0:8773/
python3 server.py --once          # imprime el JSON y sale
python3 server.py --offline       # sin tocar red (solo medido + manual)
python3 server.py --mock          # datos de ejemplo
./scripts/install-user-service.sh # servicio de usuario + verificación
python3 -m unittest discover -s tests
```

## Qué muestra

Una tarjeta por suscripción, con tres huecos cada una: **sesión / semana / mes**.
Cada hueco es una de dos cosas, y se ve distinto a propósito:

- **Gauge** (anillo con %): el proveedor **publica** esa cuota y la leemos.
- **Número en anillo punteado**: el proveedor no publica cuota de ese periodo; se
  muestra el **uso medido aquí** (tokens de entrada + salida) y el número de
  llamadas. Nunca un porcentaje inventado.

El pie de cada tarjeta dice de dónde salió el dato (`EN VIVO`, `MEDIDO`, `MANUAL`,
`SIN DATOS`, `CUOTA EN PAUSA`) y la nota del servidor dice la causa cuando algo
falla.

| Proveedor | Ventanas con cuota | Fuente real |
|---|---|---|
| OpenCode Go | rolling 5 h, semana, mes | **vivo**: `GET opencode.ai/zen/go/v1/usage` con `OPENCODE_GO_API_KEY` |
| ChatGPT Plus | 5 h y semana (Codex) | **vivo**: `GET chatgpt.com/backend-api/wham/usage` con el OAuth de `~/.codex/auth.json` |
| Claude Pro | 5 h y semana | **vivo**: `GET api.anthropic.com/api/oauth/usage` con el OAuth de `~/.claude/.credentials.json` |
| ~~Gemini Pro~~ | — | **deshabilitado** (`enabled: false`): Google cerró el cliente OAuth de Code Assist para cuentas individuales (2026-10, pide migrar a Antigravity). El adaptador sigue en el código por si vuelve a existir una fuente |

Con tres tarjetas el tablero va a **una columna por proveedor**, con los tres
anillos apilados; el `grid` se adapta al número de tarjetas (`data-n`).

## Credenciales que usa

No hay login propio: cada adaptador lee el token que dejó el CLI dueño de la
cuenta y lo refresca si está por vencer (reescribe el archivo con `chmod 600`).

| Proveedor | Archivo / variable |
|---|---|
| OpenCode Go | `OPENCODE_GO_API_KEY` en `~/.hermes/.env` |
| ChatGPT Plus | `~/.codex/auth.json` (`codex login`) |
| Claude Pro | `~/.claude/.credentials.json` (`claude auth login --claudeai`) |

Ninguna de esas credenciales vive en este repo ni se imprime en la salida.

## Uso manual (cuando no hay credencial viva)

```bash
./usage-set.py set claude session 42 --reset 2026-10-23T03:00
./usage-set.py set claude week 68
./usage-set.py list
```

También por HTTP, útil desde el teléfono:

```bash
curl -X POST http://localhost:8773/api/manual \
  -H 'Content-Type: application/json' \
  -d '{"provider":"claude","window":"session","pct":37}'
```

`manual.json` es local (no se versiona) y la tarjeta lo marca `MANUAL`: un dato
capturado a mano nunca se disfraza de vivo.

## Detalles que importan

- **Cuota de Go**: se lee del API (`/zen/go/v1/usage`), no se calcula. El 403 de
  Cloudflare (*error 1010, browser_signature_banned*) aparece si la petición viaja
  con el User-Agent de Python-urllib; por eso `OPENCODE_UA` va explícito.
- **Si el API de cuota falla**, la tarjeta pasa a `MEDIDO`: muestra los tokens en
  anillos punteados, con la causa en la nota. El cálculo por tokens × precio de
  lista se desfasa de lo que cobra el proveedor, así que nunca se pinta como cuota.
- **Ventanas de Go**: el API manda `rolling`, `weekly` y `monthly` con `percent` y
  `resetsAt`. `rolling` es la ventana móvil de 5 h.
- **Ventanas móviles**: el "libera en …" es cuándo **empieza a salir** el consumo
  más viejo de la ventana; no es un reinicio en seco.
- **Cache negativo**: una credencial muerta o un endpoint limitado no se reintenta
  cada minuto; la tarjeta dice la causa y se espacia el intento.
- **HTTP 429 no es sesión cerrada**: Anthropic limita su endpoint de uso y responde
  429 con el OAuth perfectamente válido. La tarjeta lo dice (`CUOTA EN PAUSA`) en
  vez de pedir un login que no hace falta.
- **Recarga sin tocar la tablet**: `/api/data` publica `version` (hash de `web/`);
  si cambia, la página se recarga sola.
- **Errores honestos**: cada fuente falla sola y su causa se pinta en la tarjeta.

## Kiosco en la tablet

- **Geometría adaptativa**: el ancho es fijo (1920, lo piden las tres columnas) y el
  **alto lo calcula el JS** con la proporción real de la pantalla
  (`1920 * vh / vw`, entre 1200 y 1700). En 16:10 queda el diseño original de
  1920x1200; en **4:3 (iPad) da 1920x1440** y el contenido se reparte sin franjas
  negras. Verificado con emulación de dispositivo: en 1024x768, 1133x744, 1180x820 y
  960x600 el lienzo llena ancho y alto, la rejilla cabe y ninguna tarjeta desborda.
- **Que no se apague**: la página pide `navigator.wakeLock('screen')` cuando existe
  (iPadOS 16.4+, re-pedido al volver a primer plano y en el primer toque) y lo
  ignora en silencio cuando no. **En un iPad Mini 4 no existe**: su último sistema
  es iPadOS 15.8.8, anterior al wake lock. Ahí manda iOS: *Ajustes → Pantalla y
  brillo → Bloqueo automático → Nunca*, más *Acceso guiado* para que no se salga.
  En Android 6 pasa lo mismo y lo que manda es el sistema: `Opciones de
  desarrollador → Permanecer despierto` con el cargador puesto, `Pantalla →
  Suspender` al máximo (Android 6 no tiene "Nunca"), Daydream desactivado y, si se
  usa Fully Kiosk, *Device Manager → Keep Screen On* con *Screen Off Timer = 0*.
- **iPad, en la práctica**: *Añadir a pantalla de inicio* (así corre sin barra del
  navegador), *Bloqueo automático → Nunca* y *Acceso guiado*. El padding del lienzo
  respeta `env(safe-area-inset-*)` para que el reloj no quede debajo de la barra de
  estado.
- **Tamaños de iPad Mini**: los Mini 1–5 dan un viewport CSS de **1024x768** y los
  Mini 6–7 de **1133x744**; ambos caben en el cálculo adaptativo sin tocar nada.
- **Fully Kiosk Browser 1.49.1** es la última versión que soporta Android 6 (las
  actuales piden 7+). Alternativa sin instalar nada: *Fijar pantalla* de Android 6
  con Chrome.
- **CSS conservador, a propósito**: el WebView puede ser mucho más viejo que el de
  la máquina de desarrollo. Nada de `inset` (Chromium < 87 lo ignora y el bloque
  absoluto se sale del anillo), `:has()`, `dvh`, `color-mix()` ni container
  queries. Se usan offsets explícitos, grid/flex, variables CSS y SVG.
- El servidor anota el `User-Agent` del kiosco en el journal la primera vez que
  abre la página: así se sabe qué motor trae la tablet antes de tocar el CSS.

## Qué queda fuera del repo

- `manual.json` — porcentajes capturados a mano (datos de la cuenta).
- `screenshots/` — capturas con uso real; en el repo va `docs/demo.png`,
  regenerado con `--mock`.
- Credenciales de cualquier tipo: las lee el servidor de tu propio home, nunca se
  commitean.
- `config.json` sí se publica: solo trae nombres, planes y colores.

## Licencia

MIT — ver [LICENSE](LICENSE).
