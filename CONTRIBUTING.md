# Contribuir

Gracias por pasarte. Esto es un proyecto pequeño y personal, así que las reglas
son pocas pero firmes.

## Antes de nada

```powershell
py -3.12 -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pytest tests\ -q
```

Si las pruebas no pasan en tu equipo antes de tocar nada, abre una incidencia:
eso ya es un fallo.

## La regla que no se negocia

**Ninguna habilidad habla con el sistema operativo directamente.** Todo pasa por
el guardián. Si tu aportación ejecuta algo, toca archivos o abre programas,
tiene que declarar su acción en `security/policy.yaml` y dejar que
`security/guard.py` decida.

Un pull request que se salte esa capa no se acepta, por muy útil que sea lo que
haga. El proyecto entero descansa sobre ella.

Y dos consecuencias de la misma idea:

- **Un permiso nuevo necesita una habilidad que lo use.** Hay una prueba que
  falla si añades uno a `policy.yaml` y lo dejas sin usar. Un permiso concedido
  a nada es superficie de ataque gratis.
- **Elige el nivel más restrictivo que funcione.** `permitir` solo para lo que
  no puede estropear nada; en cuanto modifique algo del equipo, `confirmar`.

## Añadir una habilidad

```python
@registro.registrar(
    nombre="mi_habilidad",
    descripcion="Qué hace. El modelo decide usarla leyendo esto, así que sé claro.",
    accion="una_accion_de_policy_yaml",
    parametros={"algo": {"type": "string", "description": "..."}},
    campo_objetivo="algo",   # Qué argumento evalúa el guardián
)
def mi_habilidad(algo: str) -> str:
    return f"Hecho con {algo}."
```

Añade la acción a `policy.yaml`, importa el módulo en `core/agent.py` y escribe
pruebas.

**Cuando algo falle, dilo en el texto que devuelves.** Los mensajes de error
terminan en `NO digas que se hizo` a propósito: el modelo los lee al redactar su
respuesta, y sin ese recordatorio convierte los fallos en éxitos inventados.
Está medido, no es paranoia.

## Estilo

El código, los comentarios y los nombres están **en español**. Es una decisión
de coherencia con un asistente que habla español; respétala aunque no sea lo
habitual.

Los comentarios explican **por qué**, no qué. `# suma uno a i` sobra; `# se
descarta el audio viejo porque para detectar una palabra en directo no vale
nada` es el tipo de cosa que hace falta dentro de seis meses.

## Pruebas

Toda aportación viene con pruebas. Las que más valor tienen aquí son las que
fijan un fallo real que ocurrió: mira `tests/test_no_mentir.py` o
`tests/test_humor.py`, donde cada caso viene de algo que pasó de verdad y su
docstring lo cuenta.

La integración continua las ejecuta en Windows con Python 3.12.

## Commits

[Conventional Commits](https://www.conventionalcommits.org): `feat:`, `fix:`,
`docs:`, `refactor:`, `test:`, `chore:`. En imperativo y sin punto final.

```
feat: añade recordatorios y alarmas
fix: la cola de audio acumulaba retraso y dejaba de detectar la palabra clave
```

## Lo que no encaja aquí

- **Automatizar el ratón o el teclado.** Permite pulsar cualquier cosa en
  cualquier programa y no hay forma de acotarlo desde la política.
- **Servicios que necesiten enviar tu voz o tus conversaciones fuera.** El
  proyecto es local por diseño; lo único que sale es una consulta puntual a
  Claude si tú pones la clave.
- **Ampliar las rutas de escritura por comodidad.** Si tu habilidad necesita
  escribir en sitios nuevos, explica por qué en el pull request.
