# Novedades

## 1.0.6 — 2026-10-27

- **Conexiones que se quedaban colgadas por la VPN**: el cifrado añade bytes a cada paquete y los más grandes ya no caben en el túnel; si la red bloquea el aviso que lo indica, una web o una copia de archivos se detiene a mitad. SwanGUI ahora ajusta el tamaño de los paquetes TCP (MSS 1360) solo para el tráfico que va por la VPN. Se puede desactivar por conexión en Editar → Avanzada → Configuración VPN.
- **«Tráfico total» ya no vuelve a cero** cada vez que strongSwan renueva las claves del túnel (normalmente cada hora): SwanGUI suma los bytes de las claves anteriores mientras dure la conexión.

## 1.0.5 — 2026-10-27

- Ventana principal más compacta (600 × 520): un solo botón Conectar/Desconectar, las acciones menos usadas en «Más ▾» y lista de conexiones más baja, con barra de desplazamiento si hay muchas.
- Detalles más claros: DNS en formato corto (modo · servidor (dominios) ✓), «Inicio» en lugar de «Conectado desde», flechas de tráfico en color y más separación entre dato y valor.
- Gráfica nueva:
  - Selector de periodo: último minuto, 10 min, 30 min o última hora.
  - Cuadrícula, eje de tiempo y dos ejes con título: «Tráfico» (B/s, KiB/s o MiB/s) a la izquierda y «Latencia (ms)» a la derecha.
  - Leyenda centrada con los valores actuales y valores de cada momento al pasar el ratón.
  - Una ráfaga antigua ya no aplasta la escala: se dibuja cortada con ▲ y su valor.
  - Recibido en azul, enviado en naranja y latencia en gris discontinuo.
- La gráfica de cada conexión guarda su sesión actual (como mucho la última hora) y la conserva al reiniciar SwanGUI; empieza de nuevo al desconectar o al reiniciar el equipo. SwanGUI también guarda los datos al cerrar sesión o apagar.
- La latencia se mide contra el DNS de la VPN y, si un equipo no responde al ping, SwanGUI prueba el siguiente. Antes, con VPN cuyo primer equipo no responde, no había latencia ni «Calidad».

## 1.0.4 — 2026-10-26

- Los errores de SwanGUI quedan ahora también en el registro del sistema, para poder diagnosticarlos después. Incluye el caso de una VPN que conecta pero cuyo DNS no se aplica, que antes solo se veía en la notificación. Para verlos: `journalctl -t swangui-helper -p warning`.

## 1.0.3 — 2026-10-26

- Ya no aparece la ventana de contraseña de administrador al volver de una suspensión: SwanGUI deja de consultar el estado de la VPN mientras el equipo se suspende o la sesión no está activa, y reanuda las consultas al despertar.
- Nuevo enlace de Buy Me a Coffee: https://buymeacoffee.com/frankmncrf

## 1.0.2 — 2026-10-25

- Al dejar el puntero sobre el icono de la bandeja aparece una tarjeta con los detalles de cada conexión: círculo con el color del estado, calidad (latencia y pérdidas), IP virtual, servidor, tiempo conectado, recibido/enviado, velocidad, redes remotas, DNS, próxima renegociación y aviso de caducidad del certificado.
- La tarjeta incluye una gráfica de tráfico y botones para conectar, desconectar y abrir SwanGUI.
- Opcional: mostrar la IP pública en la tarjeta (Preferencias; desactivado por defecto).
- El icono muestra cuántas VPN hay activas cuando son varias y parpadea suavemente mientras conecta.
- El globo de avisos aparece ahora justo debajo del icono aunque el panel no indique su posición.
- Colores con más contraste en las gráficas: recibido en azul, enviado en naranja y latencia en gris discontinuo.

## 1.0.1 — 2026-10-25

- Los avisos de conexión, desconexión y errores se muestran en un globo propio de SwanGUI junto al icono de la bandeja, como en FortiClient.
- Nueva opción en Preferencias → General → «Estilo de los avisos» para volver a las notificaciones del escritorio.
- El README incluye los enlaces de descarga de los paquetes.

## 1.0.0 — 2026-10-25

Primera versión pública.

- Conexiones IPsec IKEv1/IKEv2 con configuración al estilo FortiClient y plantillas por fabricante.
- Importar/exportar: FortiClient, swanctl, MikroTik, pfSense/OPNsense, Windows, Cisco IOS, Apple y Android; exportaciones cifradas con contraseña.
- DNS dividido con comprobación de que el DNS de la VPN responde.
- Kill switch con nftables, también para contenedores y máquinas virtuales.
- Siempre conectada, redes de confianza, reconexión al despertar y detección de portales cautivos.
- Diagnóstico, prueba de IP pública y fugas DNS, registro, historial y gráfica de tráfico.
- Permisos en dos niveles con polkit y registro de auditoría.
- Interfaz en español e inglés.
