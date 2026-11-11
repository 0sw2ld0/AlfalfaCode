# La CLI de AlfalfaCode (`alfalfa`). Este archivo vive en github.com/0sw2ld0/AlfalfaCode
# (distribucion/homebrew) y se copia al tap 0sw2ld0/homebrew-tap; la URL y las huellas las
# cambia distribucion/actualizar-tap.sh a partir de cada release.
#
# No compila nada: usa el motor que ya va dentro de la app del DMG (un Python 3.13 propio
# con alfalfa y sus dependencias fijadas, y los ayudantes Swift compilados). Es lo mismo
# que instala `curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh`.
require "open3"
require "tmpdir"

class Alfalfa < Formula
  desc "Arnés de agentes de programación: Ollama, OpenAI, Anthropic y modelos gratis"
  homepage "https://alfalfacode.cuycoders.dev"
  url "https://github.com/0sw2ld0/AlfalfaCode/releases/download/v0.99.2/AlfalfaCode-0.99.2.dmg"
  sha256 "23cacadb3a4c44c4fa93b84e419ce50523524f2b21fca634d062522fa1e51952"
  license "MIT"

  livecheck do
    url :stable
    strategy :github_latest
  end

  # El bottle lo hace distribucion/botella.sh con el motor del DMG tal cual y va en la
  # release. :any_skip_relocation: Homebrew no reescribe sus binarios (siguen con la firma
  # Developer ID del equipo FZ8TW9AWLE) y no pide las CLT al día, como sí haría `install`.
  bottle do
    root_url "https://github.com/0sw2ld0/AlfalfaCode/releases/download/v0.99.2"
    sha256 cellar: :any_skip_relocation, arm64_sonoma: "36e525512f5247e6996a0aeecf203cfe3b48874bffca122ff64126369c256e00"
  end

  depends_on arch: :arm64
  depends_on macos: :sonoma

  # El bytecode va compilado con huella dentro del motor y el lanzador usa -B: que Homebrew
  # no lo limpie, o cada arranque volvería a interpretar las fuentes.
  skip_clean "libexec"

  # Sólo con --build-from-source (o sin bottle): lo mismo que deja botella.sh.
  def install
    # Homebrew entra en la única carpeta del DMG (la app) si el acceso a Aplicaciones no cuenta.
    contenidos = [buildpath/"Contents", buildpath/"AlfalfaCode.app/Contents"].find(&:directory?)
    odie "El DMG no trae AlfalfaCode.app" if contenidos.nil?
    comprobar_cuybrowser(contenidos)

    (libexec/"Resources").install contenidos/"Resources/motor"
    libexec.install contenidos/"Helpers"
    # empaquetado.py reconoce así la CLI suelta y actualizacion.py sabe que la actualiza brew.
    (libexec/"instalacion").write "brew\n#{version}\n"

    # Al instalar así, Homebrew reescribe el identificador de las bibliotecas (.so de Rust
    # con @rpath) y las vuelve a firmar ad hoc. El Python lleva hardened runtime con la firma
    # del equipo, que no cargaría una biblioteca firmada ad hoc: todo el motor va ad hoc, sin
    # runtime. La integridad ya la garantiza la huella SHA-256 del DMG. CuyBrowser sólo
    # enlaza bibliotecas de macOS y conserva su firma con los permisos de JavaScriptCore.
    magicos = [0xcafebabe, 0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe]
    cuybrowser = (libexec/"Helpers/cuybrowser").to_s
    macho = Dir[libexec/"**/*"].select do |f|
      f != cuybrowser && File.file?(f) && !File.symlink?(f) && File.size(f) >= 4 &&
        magicos.include?(File.binread(f, 4).unpack1("N"))
    end
    macho.each { |f| system "/usr/bin/codesign", "--force", "--sign", "-", f }

    (bin/"alfalfa").write <<~SH
      #!/bin/sh
      # AlfalfaCode (Homebrew): el motor está en ../libexec respecto a este archivo, sin rutas fijas.
      yo=$0
      while [ -L "$yo" ]; do
        d=$(cd -P -- "$(dirname -- "$yo")" && pwd)
        l=$(readlink "$yo")
        case "$l" in /*) yo=$l ;; *) yo=$d/$l ;; esac
      done
      keg=$(cd -P -- "$(dirname -- "$yo")/.." && pwd)
      exec "$keg/libexec/Resources/motor/alfalfa" "$@"
    SH
    chmod 0755, bin/"alfalfa"
  end

  def comprobar_cuybrowser(contenidos)
    clave = "AlfalfaCuyBrowserVersion"
    plist = (contenidos/"Info.plist").to_s
    tipo, estado = Open3.capture2("/usr/bin/plutil", "-type", clave, plist, err: File::NULL)
    return unless estado.success? # Compatibilidad con los DMG anteriores a CuyBrowser.

    version, estado = Open3.capture2("/usr/bin/plutil", "-extract", clave, "raw", "-o", "-", plist)
    version = version.strip
    odie "Versión de CuyBrowser inválida" unless tipo.strip == "string" && estado.success? &&
                                                   version.match?(/\A\d+\.\d+\.\d+\z/)

    ejecutable = contenidos/"Helpers/cuybrowser"
    odie "La app no trae CuyBrowser #{version}" unless ejecutable.file? && ejecutable.executable? && !ejecutable.symlink?

    system "/usr/bin/codesign", "--verify", "--strict", ejecutable.to_s
    Dir.mktmpdir do |casa|
      salida, estado = Open3.capture2({ "HOME" => casa, "PATH" => "/usr/bin:/bin" }, ejecutable.to_s, "--version",
                                      chdir: casa, unsetenv_others: true)
      odie "El ejecutable no declara CuyBrowser #{version}" unless estado.success? && salida.strip == version
    end
  end

  def caveats
    <<~EOS
      Para empezar: alfalfa "tu encargo" (la primera vez te ofrece modelos gratis).
      Se actualiza con `brew upgrade alfalfa`. Tus sesiones y ajustes viven en ~/.alfalfa.
      La app de Mac: brew install --cask 0sw2ld0/tap/alfalfacode
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/alfalfa --version")
    assert_match "brew", shell_output("#{libexec}/Resources/motor/python/bin/python3 -I -B -c " \
                                      "'import alfalfa.empaquetado as e; print(e.instalacion())'")
    cuybrowser = libexec/"Helpers/cuybrowser"
    system "/usr/bin/codesign", "--verify", "--strict", cuybrowser.to_s if cuybrowser.exist?
  end
end
