# La CLI de AlfalfaCode (`alfalfa`). Este archivo vive en github.com/0sw2ld0/AlfalfaCode
# (distribucion/homebrew) y se copia al tap 0sw2ld0/homebrew-tap; la URL y las huellas las
# cambia distribucion/actualizar-tap.sh a partir de cada release.
#
# No compila nada: usa el motor que ya va dentro de la app del DMG (un Python 3.13 propio
# con alfalfa y sus dependencias fijadas, y los ayudantes Swift compilados). Es lo mismo
# que instala `curl -fsSL https://cuycoders.dev/alfalfa/instalar.sh | sh`.
class Alfalfa < Formula
  desc "Arnés de agentes de programación: Ollama, OpenAI, Anthropic y modelos gratis"
  homepage "https://cuycoders.dev/alfalfa"
  url "https://github.com/0sw2ld0/AlfalfaCode/releases/download/v0.99.1/AlfalfaCode-0.99.1.dmg"
  sha256 "26d0897b1ab8f31cf877709c10ca2006c46b74afe948d3cf77e117b71018541e"
  license "MIT"

  livecheck do
    url :stable
    strategy :github_latest
  end

  # El bottle lo hace distribucion/botella.sh con el motor del DMG tal cual y va en la
  # release. :any_skip_relocation: Homebrew no reescribe sus binarios (siguen firmados por
  # Apple) y no pide las herramientas de línea de órdenes al día, como sí haría `install`.
  bottle do
    root_url "https://github.com/0sw2ld0/AlfalfaCode/releases/download/v0.99.1"
    sha256 cellar: :any_skip_relocation, arm64_sonoma: "255977bc45e9fb95ebbf7b25d6f4fc61083f851ac925d07755f86b0f9d61e85b"
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

    (libexec/"Resources").install contenidos/"Resources/motor"
    libexec.install contenidos/"Helpers"
    # empaquetado.py reconoce así la CLI suelta y actualizacion.py sabe que la actualiza brew.
    (libexec/"instalacion").write "brew\n#{version}\n"

    # Al instalar así, Homebrew reescribe el identificador de las bibliotecas (.so de Rust
    # con @rpath) y las vuelve a firmar ad hoc. El Python lleva hardened runtime con la firma
    # del equipo, que no cargaría una biblioteca firmada ad hoc: todo el motor va ad hoc, sin
    # runtime. La integridad ya la garantiza la huella SHA-256 del DMG.
    magicos = [0xcafebabe, 0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe]
    macho = Dir[libexec/"**/*"].select do |f|
      File.file?(f) && !File.symlink?(f) && File.size(f) >= 4 && magicos.include?(File.binread(f, 4).unpack1("N"))
    end
    macho.each { |f| system "/usr/bin/codesign", "--force", "--sign", "-", f, out: File::NULL }

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
  end
end
