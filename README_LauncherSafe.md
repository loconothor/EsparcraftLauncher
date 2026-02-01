Launcher Genérico de Servidores Java (Windows)



\- Requisitos

&nbsp; - Python 3.x (Tkinter)

&nbsp; - Java en PATH



\- Uso

&nbsp; - Ejecuta: python launcher.py

&nbsp; - Cabecera de consola muestra: Estado, Jugadores, Tiempo online y Puerto

&nbsp; - Botones Iniciar/Detener en la cabecera derecha

&nbsp; - Consola con logs coloreados por severidad

&nbsp; - El tiempo online empieza cuando el servidor entra en estado "En ejecución"

\- Empaquetar a .exe

&nbsp; - pyinstaller --onefile --noconsole launcher.py

&nbsp; - dist/launcher.exe



