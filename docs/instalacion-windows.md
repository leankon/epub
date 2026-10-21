# Instalación en Windows (paso a paso)

Hay dos caminos. Elegí **uno**:

* **Camino A — con permisos de administrador (WSL + Ubuntu).** Recomendado: es el entorno donde se probó todo.
* **Camino B — sin permisos de administrador (Windows nativo).** Más trabajo; el script de Windows no está
  probado todavía, puede necesitar ajustes. EPUB→PDF probablemente no funcione en este camino.

---

## Camino A — con administrador (WSL)

### 1. Instalar Ubuntu dentro de Windows (una sola vez)
1. Abrir **cmd como administrador** (Inicio → escribir `cmd` → clic derecho → *Ejecutar como administrador*).
2. Ejecutar:
   ```cmd
   wsl --install -d Ubuntu-24.04
   ```
3. Reiniciar la computadora.
4. Al volver se abre la ventana de **Ubuntu**: crear un usuario y una contraseña (la contraseña no se ve al
   escribirla, es normal).

### 2. Instalar los programas (una sola vez, dentro de Ubuntu)
Copiar y pegar en la ventana de Ubuntu (clic derecho pega):
```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv postgresql \
  tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng tesseract-ocr-osd \
  libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress \
  libpango-1.0-0 libpangoft2-1.0-0 default-jre-headless unzip
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

### 3. Bajar y preparar el proyecto (una sola vez)
```bash
cd ~
git clone -b claude/happy-thompson-b1cgmq https://github.com/leankon/epub.git
cd epub
./scripts/setup.sh --with-epubcheck
```
Tarda unos minutos. Al final muestra una lista de herramientas con `[OK ]`.

### 4. Prender la aplicación (cada vez que la quieras usar)
1. Abrir **Ubuntu** desde el menú Inicio.
2. Ejecutar:
   ```bash
   cd ~/epub
   ./scripts/dev.sh
   ```
3. Cuando aparezca `PDF Workstation is running: http://127.0.0.1:3000`, abrir esa dirección en Chrome o Edge.
4. Para apagar: volver a la ventana de Ubuntu y apretar **Ctrl+C**.

---

## Camino B — sin administrador (Windows nativo)

### 1. Descargar los programas (una sola vez)
Crear la carpeta `C:\Users\TU_USUARIO\apps` (reemplazar `TU_USUARIO` por tu usuario de Windows).

| Programa | Dónde | Cómo |
|---|---|---|
| Git | git-scm.com/download/win → *Portable* ("thumbdrive edition") | Descomprimir en `apps\git` |
| Python 3.12 | python.org/downloads → 3.12.x → *Windows installer (64-bit)* | Al instalar: **desmarcar** "for all users", **marcar** "Add python.exe to PATH" |
| Node.js 22 | nodejs.org → *Windows Binary (.zip)* 64-bit | Descomprimir; renombrar la carpeta a `apps\node` |
| PostgreSQL 16 | enterprisedb.com/download-postgresql-binaries → Windows x86-64, versión 16 | Descomprimir; queda `apps\pgsql` |
| Tesseract (OCR) | github.com/UB-Mannheim/tesseract/wiki → instalador 64-bit | Instalar en una carpeta tuya (ej. `apps\tesseract`) y **marcar Spanish** en *Additional language data*. Si pide administrador, el OCR no va a estar disponible |
| LibreOffice (opcional: Word/Excel/PowerPoint → PDF) | portableapps.com → LibreOffice Portable | Instalar en `apps\LibreOfficePortable` |

### 2. Agregar las carpetas al PATH (sin administrador)
1. Inicio → buscar **"Editar las variables de entorno de esta cuenta"** (la que dice *de esta cuenta*).
2. Seleccionar `Path` → **Editar** → **Nuevo**, y agregar una por una:
   ```
   C:\Users\TU_USUARIO\apps\git\cmd
   C:\Users\TU_USUARIO\apps\node
   C:\Users\TU_USUARIO\apps\pgsql\bin
   C:\Users\TU_USUARIO\apps\tesseract
   ```
3. Aceptar todo y **cerrar y volver a abrir cmd**.
4. Comprobar en cmd (cada uno debe mostrar una versión):
   ```cmd
   git --version
   python --version
   node --version
   pg_ctl --version
   tesseract --list-langs
   ```
   `tesseract --list-langs` tiene que incluir `spa`.

### 3. Bajar y preparar el proyecto (una sola vez, en cmd)
```cmd
cd %USERPROFILE%
git clone -b claude/happy-thompson-b1cgmq https://github.com/leankon/epub.git
cd epub
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Setup
```
Si usás LibreOffice Portable, abrir el archivo `.env` (en la carpeta `epub`, se crea en el primer arranque) con el
Bloc de notas y agregar al final:
```
PDFWS_SOFFICE_CMD=C:\Users\TU_USUARIO\apps\LibreOfficePortable\App\libreoffice\program\soffice.exe
```

### 4. Prender la aplicación (cada vez)
```cmd
cd %USERPROFILE%\epub
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```
Abrir **http://127.0.0.1:3000**. Para apagar: **Ctrl+C** en la ventana de cmd.

---

## Si algo falla
* Copiar el mensaje de error completo y los últimos renglones de `data\run\backend.log`
  (o `data/run/backend.log` en Ubuntu).
* Errores comunes: ver [troubleshooting.md](troubleshooting.md).
* En una compu del trabajo puede estar bloqueado instalar programas o ejecutar scripts de PowerShell; en ese
  caso hay que pedirlo a sistemas.
