<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/banner-light.svg">
  <img alt="ATOM Organizer" src="docs/assets/banner.svg" width="100%">
</picture>

[![Última versión](https://img.shields.io/github/v/release/saezro/atom-organizer?style=flat-square&color=EE763C&label=%C3%BAltima%20versi%C3%B3n)](https://github.com/saezro/atom-organizer/releases/latest)
![Plataformas](https://img.shields.io/badge/plataformas-Windows%20%7C%20Linux-EE763C?style=flat-square)
[![Descargas](https://img.shields.io/github/downloads/saezro/atom-organizer/total?style=flat-square&color=EE763C&label=descargas)](https://github.com/saezro/atom-organizer/releases)

</div>

## Qué es

ATOM Organizer es la aplicación de escritorio de Aerotools para organizar y procesar las imágenes de tus vuelos de dron, tanto RGB como térmicas, con una nomenclatura homogénea lista para su análisis.

## Descarga

<div align="center">

[**Descargar para Windows**](https://github.com/saezro/atom-organizer/releases/latest) &nbsp;·&nbsp; [**Descargar para Linux**](https://github.com/saezro/atom-organizer/releases/latest)

</div>

En la página de la última versión encontrarás, dentro de *Assets*:

| Sistema | Archivo | Cómo instalarlo |
| --- | --- | --- |
| Windows | `ATOM-Organizer-Setup-vX.Y.Z.exe` | Ejecuta el instalador y sigue los pasos. No requiere permisos de administrador. |
| Linux | `ATOM_Organizer-vX.Y.Z-x86_64.AppImage` | Dale permisos de ejecución (`chmod +x`) y ábrelo con doble clic. |

La aplicación te avisa cuando hay una versión nueva.

## Cómo entrar

Al abrir la aplicación, inicia sesión de una de estas dos formas:

- **Usuario y contraseña** de la Suite.
- **Entrar con Google**, con la cuenta asociada a tu acceso.

Si no tienes acceso, pídelo a tu contacto en Aerotools.

## Requisitos

- **Windows:** 10 o 11, 64 bits.
- **Linux:** distribución de 64 bits (x86_64) con soporte para AppImage.
- Conexión a internet para iniciar sesión y comprobar actualizaciones.
- Espacio libre en disco suficiente para las copias de tus vuelos.

## Preguntas frecuentes

**¿Modifica mis imágenes originales?**
*Procesado RGB* trabaja siempre sobre copias y nunca toca el origen. Las opciones de *Extracción TMC* y *Convertir DJI a TIFF* mueven o eliminan archivos en la carpeta de origen: en material irreemplazable, trabaja sobre una copia del vuelo.

**¿Funciona el procesado térmico en Linux?**
Parcialmente. La conversión radiométrica DJI a TIFF y la extracción de archivos `.TMC` dependen de componentes solo disponibles en Windows. Para el flujo térmico completo, usa la versión de Windows. El resto de funciones son iguales en ambos sistemas.

**Windows me muestra un aviso al instalar.**
Es el aviso habitual de SmartScreen para aplicaciones nuevas. Elige *Más información* y después *Ejecutar de todos modos*.

**¿Cómo actualizo?**
La aplicación te lo propone al detectar una versión nueva. También puedes descargar el instalador más reciente desde [Releases](https://github.com/saezro/atom-organizer/releases/latest) e instalarlo encima.

## Soporte

¿Algo no funciona o tienes una sugerencia? Abre una incidencia en [GitHub Issues](https://github.com/saezro/atom-organizer/issues) indicando tu sistema operativo, la versión de la aplicación y qué estabas haciendo. No adjuntes datos de clientes ni credenciales.

También puedes escribir a tu contacto habitual en Aerotools.

---

<sub>Documentación para desarrolladores: [docs/DESARROLLO.md](docs/DESARROLLO.md)</sub>
