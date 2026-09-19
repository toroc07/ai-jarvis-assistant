# Jarvis

Asistente personal por voz para Windows. Habla español, funciona **entero en tu
equipo** y solo puede tocar lo que le autorices explícitamente.

Vive escondido en la bandeja del sistema. Dices «hey Jarvis» y aparece un orbe
que se mueve con el sonido mientras habláis; cuando te despides, desaparece.

Nada de lo que dices sale de tu ordenador. El modelo de lenguaje, el
reconocimiento de voz, la síntesis y la verificación de locutor corren en local.
La única conexión externa opcional es a la API de Claude, y solo si tú pones una
clave.

---

## Qué necesitas

- **Windows 10 u 11**
- **Python 3.12** (3.13+ aún no tiene ruedas para todas las dependencias de voz)
- **[Ollama](https://ollama.com)** con un modelo descargado
- **8 GB de RAM libres** como mínimo. Con menos, usa un modelo más pequeño
- Un **micrófono**

Probado en un portátil con AMD Ryzen AI 7 350, 32 GB de RAM y gráfica integrada.
No hace falta GPU dedicada.

## Instalación

```powershell
git clone https://github.com/toroc07/ai-jarvis-assistant.git
cd ai-jarvis-assistant

py -3.12 -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu

ollama pull qwen3:8b

copy .env.example .env
```

Abre `.env` y pon tu nombre en `JARVIS_USUARIO`. Lo demás ya viene con valores
que funcionan.

La primera vez que arranques se descargan solos los modelos de voz (unos
250 MB: el detector de la palabra clave, Whisper para transcribir, Piper para
hablar y ECAPA-TDNN para reconocer tu voz).

## Arrancar

```powershell
.\venv\Scripts\pythonw.exe jarvis.py
```

Arranca invisible. Al segundo ya te escucha; unos cinco segundos después
termina de cargar lo demás.

Para desarrollar, `python jarvis.py` deja la consola detrás con los mensajes.

Para que arranque solo al encender el PC:

```powershell
.\venv\Scripts\python.exe inicio_automatico.py --activar
```

Crea accesos directos en tu carpeta de Inicio (los de Jarvis y los de Ollama,
si lo encuentra). Se ven, se entienden y se borran a mano; `--desactivar` los
quita.

| | |
|---|---|
| Hablarle | decir **«hey Jarvis»** |
| Hablarle sin decirlo | «Hablar con Jarvis» en la bandeja |
| Terminar la conversación | despedirte, o clic derecho en el orbe |
| Apagarlo del todo | decir «Jarvis, apágate», Ctrl+C, o la bandeja |
| **Parada de emergencia** | **Ctrl+Alt+J** |
| Chat escrito | menú de la bandeja |
| Mover el orbe | arrastrarlo |

**Cerrar no es apagar.** Cerrar la ventana o despedirte deja a Jarvis
escuchando en segundo plano, con la conversación viva. Apagarlo termina el
proceso, y al volver a ejecutarlo empieza una sesión nueva.

---

## El modelo de seguridad

Es la parte más cuidada del proyecto, porque un asistente que puede abrir
programas y tocar archivos puede romperte cosas.

**Ninguna habilidad habla con el sistema operativo directamente.** Todas piden
permiso al guardián (`security/guard.py`), que consulta `security/policy.yaml`
y solo entonces las deja pasar. El diseño asume que el modelo puede
equivocarse o ser manipulado por lo que le digas, así que las comprobaciones no
se fían de lo que el modelo *dice* que va a hacer: verifican la ruta ya resuelta
(tras seguir enlaces y resolver `..`), el comando ya montado y la extensión real
del archivo.

`policy.yaml` es una **lista blanca**: lo que no está, no se ejecuta. Cada
acción tiene un nivel:

| Nivel | Qué significa |
|---|---|
| `permitir` | Se ejecuta sin preguntar |
| `confirmar` | Te enseña qué va a hacer y espera tu sí |
| `prohibir` | Se rechaza siempre, ni confirmando |

Cuatro decisiones que conviene conocer:

- **Borrar no borra.** Los archivos van a `data/papelera/`, y sobrescribir deja
  copia previa. Un error de Jarvis es recuperable.
- **Jarvis no puede editar su propia política.** `security/` está en las rutas
  prohibidas, que ganan sobre cualquier otro permiso.
- **En el diálogo de permiso, el botón por defecto es «No».** Si apruebas por
  inercia dándole al intro, la respuesta es que no.
- **No se automatiza el navegador.** Sería lo natural para "búscame una canción
  en YouTube", pero controlar el ratón sobre la ventana que haya delante
  permite pulsar cualquier cosa en cualquier programa. En su lugar se consulta
  la búsqueda y se abre la dirección del vídeo directamente.

### Parada de emergencia

**Ctrl+Alt+J** desde cualquier parte, un botón rojo en la ventana, o el menú de
la bandeja. Detiene a Jarvis en el acto: deja de ejecutar cualquier acción,
aunque siga conversando.

- **Detener no pide confirmación.** Un interruptor que pregunta «¿seguro?» no
  sirve para una emergencia. Reactivar sí la pide y enseña por qué se detuvo.
- **La parada sobrevive al reinicio.** Se guarda en disco: si Jarvis se cierra
  detenido, al volver sigue detenido.
- **Jarvis no puede desactivárselo.** No existe herramienta ni acción que lo
  rearme, y hay pruebas que fallan si alguien añade una.
- **Se activa solo** si Jarvis intenta dos acciones prohibidas en cinco
  minutos. Un intento suelto es el modelo equivocándose; varios seguidos son
  otra cosa, y da igual si es un fallo suyo o alguien manipulándolo.

### Que solo te responda a ti

Lees cinco frases, se extrae de cada una un vector de 192 números con
ECAPA-TDNN que resume cómo suena tu voz, y se guarda la media. En cada
activación se compara.

Es un **filtro de conveniencia, no autenticación**: evita que te lo activen
otras personas o la televisión, pero una grabación de buena calidad podría
engañarlo, y por eso las acciones que tocan el sistema siguen pidiendo
confirmación.

La verificación va en **dos fases**, con el listón repartido según lo que está
en juego: uno más bajo para abrir el orbe con el «hey Jarvis» (1,5 segundos de
audio, y abrir un círculo en pantalla es inofensivo) y el calibrado de verdad
sobre la petición completa, que dura varios segundos y sí permite distinguir
voces.

---

## Cómo está montado

```
jarvis.py              Punto de entrada: aplicación de escritorio con voz
main.py                Versión de consola, sin interfaz ni voz
revisar.py             Revisa qué habilidades te han faltado

core/brain.py          Enruta entre el modelo local y Claude
core/agent.py          El bucle: petición → razonar → herramientas → responder
core/memory.py         SQLite: conversaciones, sesiones, hechos sobre ti
core/carencias.py      Registra lo que Jarvis NO supo hacer
core/honestidad.py     Detecta cuando afirma haber hecho algo sin hacerlo
core/texto.py          Limpia emojis y Markdown antes de hablar

security/policy.yaml   Qué puede hacer y dónde. Se edita a mano
security/guard.py      Valida cada acción. Nada lo esquiva
security/parada.py     El interruptor de emergencia

skills/                Las capacidades, cada una declarando qué permiso necesita
voice/                 Palabra clave, transcripción, síntesis, locutor, ruido
ui/                    Orbe, ventana de chat, bandeja, diálogos
```

### Añadir una habilidad

```python
@registro.registrar(
    nombre="mi_habilidad",
    descripcion="Qué hace, en la forma en que el modelo decide usarla.",
    accion="una_accion_de_policy_yaml",
    parametros={"algo": {"type": "string", "description": "..."}},
    campo_objetivo="algo",   # Qué argumento evalúa el guardián
)
def mi_habilidad(algo: str) -> str:
    return f"Hecho con {algo}."
```

Añade su acción a `policy.yaml` con el nivel que merezca e impórtala en
`core/agent.py`. El guardián se encarga del resto.

---

## Notas de ingeniería

Cosas que se midieron durante el desarrollo y explican decisiones que de otra
forma parecerían arbitrarias.

**El modelo de 4B no sirve, aunque sea más rápido.** Qwen3 4B da 14,4 tok/s
frente a 10,7 del 8B, pero ignora la orden de no razonar y vuelca su monólogo
interno en la respuesta: 1744 tokens donde el 8B usa 94.

**Las definiciones de herramientas cuestan 9 segundos la primera vez.** Son unos
930 tokens de prompt. Ollama reaprovecha ese trabajo después (baja a 0,3 s), así
que Jarvis lo hace al arrancar, en segundo plano. El calentamiento va **en
streaming a propósito**: una petición sin streaming no deja preparado lo que
necesita una con streaming.

**Un modelo de 8B no llama a las herramientas de forma fiable.** A veces escribe
la llamada como texto plano en vez de emitirla. `_rescatar_llamada_en_texto()`
la recupera, comprobando que el nombre exista de verdad en el registro.

**Ni dice la verdad de forma fiable.** Llegó a contestar «Reproduciendo Smells
Like Teen Spirit» sin haber ejecutado nada. La regla está en el prompt, pero
`core/honestidad.py` la respalda en código: si afirma haber actuado sin
herramientas, se le da una oportunidad de corregirse y, si insiste, se sustituye
su respuesta por la verdad.

**La cola de audio necesita tope.** El micrófono entrega un bloque cada 80 ms y
analizarlo cuesta unos 100. Sin límite, el retraso crece sin parar y a los pocos
minutos Jarvis analiza audio de hace un minuto. Para detectar una palabra en
directo, el audio viejo no vale nada.

**La limpieza de ruido se mide con lo que importa.** La relación señal/ruido
clásica dice que empeora la señal, pero esa métrica castiga a toda esta familia
de algoritmos. Medido sobre el parecido de la huella vocal —que es lo que de
verdad decide si te reconoce— mejora +0,019 con ruido y no toca nada sin él.
Se probó `noisereduce` y salió peor en ambos ejes.

**Un permiso que nadie usa se quita, no se deja "por si acaso".** Llegó a
haber seis, uno de ellos `ejecutar_comando`, el de más riesgo del proyecto. Hay
una prueba que falla si vuelve a aparecer alguno. La validación de comandos
sigue en el guardián, probada y lista para cuando haya una habilidad que de
verdad la necesite.

**Los chistes van en una colección escrita.** El modelo local los inventa sin
remate: «un hombre pide un vaso de whisky; el barman dice: lo siento, no tengo
vaso, pero puedo prepararte un vaso de whisky». Un chiste depende de una frase
final colocada con precisión, y eso un 8B no lo sostiene.

---

## Rendimiento

Medido en el equipo de referencia, con qwen3:8b sobre gráfica integrada:

| | |
|---|---|
| Hasta empezar a escuchar | ~1 s |
| Respuesta en conversación | ~5 s hasta la primera palabra |
| Respuesta que usa una herramienta | 12-16 s |
| Generación | 10,7 tok/s |

Una petición con herramienta cuesta el doble porque necesita dos vueltas al
modelo: una para decidir qué usar y otra para redactar con el resultado.

Ollama descarta las gráficas integradas por defecto. Se activan con la variable
de entorno `OLLAMA_IGPU_ENABLE=1`; sin ella todo corre en CPU. Para comprobarlo,
`ollama ps` debe decir `100% GPU`.

---

## Qué falta

**Funciones**

- Alarmas y temporizadores
- Búsqueda web general (hoy solo busca en YouTube)
- Leer tus documentos y PDFs con búsqueda semántica
- Calendario, correo y mensajería
- Domótica

**Deuda conocida**

- El camino de Claude está escrito y enrutado, pero **nunca se ha ejecutado**:
  sin clave de API no se llega a llamar. El enrutado sí está probado; la llamada
  en sí, no.
- La interfaz no tiene pruebas propias más allá de comprobar que construye. Si
  alguien toca el orbe o los diálogos, nada le avisa de que lo rompió.

**Limitaciones que no se arreglan con más código**

- **Velocidad**: unos 5 segundos hasta la primera palabra, 12-16 si usa una
  herramienta. Es el techo de un modelo de 8B en gráfica integrada.
- **La palabra es «hey Jarvis»**, no «Jarvis». Es el modelo preentrenado que
  ofrece openWakeWord; entrenar uno propio es trabajo aparte y de resultado
  incierto.
- **La verificación de voz es floja** con audio corto. Por eso va en dos fases.

---

Jarvis **anota solo** lo que le pides y no sabe hacer, en `data/carencias.jsonl`.
`python revisar.py` lo enseña agrupado y ordenado por cuántas veces lo has
pedido, que es una forma bastante honesta de decidir qué construir primero.

## Pruebas

```powershell
.\venv\Scripts\python.exe -m pytest tests\ -q
```

286 pruebas, y la integración continua las ejecuta en cada cambio. Las del
guardián cubren los intentos de fuga que importan: salir de
las carpetas permitidas con `..`, encadenar comandos detrás de uno legítimo,
llegar a las credenciales, y que Jarvis reescriba su propia política.

## Privacidad

Todo lo que dices, tu huella vocal, tus conversaciones y lo que Jarvis recuerde
de ti se quedan en `data/`, que está excluida del repositorio. Las únicas
conexiones salientes son a Ollama en tu propia máquina, a YouTube si pides
música, y a Hugging Face una vez para descargar los modelos de voz.

## Licencia

Ver [LICENSE](LICENSE).
