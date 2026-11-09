# La app de Mac de AlfalfaCode. Este archivo vive en github.com/0sw2ld0/AlfalfaCode
# (distribucion/homebrew) y se copia al tap 0sw2ld0/homebrew-tap; la versión y la huella
# las cambia distribucion/actualizar-tap.sh a partir de cada release.
cask "alfalfacode" do
  version "0.99.1"
  sha256 "26d0897b1ab8f31cf877709c10ca2006c46b74afe948d3cf77e117b71018541e"

  url "https://github.com/0sw2ld0/AlfalfaCode/releases/download/v#{version}/AlfalfaCode-#{version}.dmg",
      verified: "github.com/0sw2ld0/AlfalfaCode/"
  name "AlfalfaCode"
  desc "Arnés de agentes de programación: Ollama, OpenAI, Anthropic y modelos gratis"
  homepage "https://cuycoders.dev/alfalfa"

  livecheck do
    url :url
    strategy :github_latest
  end

  # La app se actualiza sola (lee version.json de la última release, comprueba huella,
  # firma y notarización). Con esto `brew upgrade` no se pelea con ella: no la toca salvo
  # con --greedy, y entonces instala lo mismo que ya trajo el actualizador.
  auto_updates true
  depends_on arch: :arm64
  depends_on macos: :sonoma

  app "AlfalfaCode.app"

  # Sólo lo de la app. Las sesiones y ajustes de ~/.alfalfa son del usuario (y de la CLI).
  zap trash: [
    "~/Library/Caches/com.cuycoders.alfalfacode",
    "~/Library/HTTPStorages/com.cuycoders.alfalfacode",
    "~/Library/Preferences/com.cuycoders.alfalfacode.plist",
    "~/Library/Saved Application State/com.cuycoders.alfalfacode.savedState",
    "~/Library/WebKit/com.cuycoders.alfalfacode",
  ]

  caveats <<~EOS
    La app trae su propio motor: no hace falta instalar nada más.
    Para usar AlfalfaCode también desde la terminal:
      brew install 0sw2ld0/tap/alfalfa
    Tus sesiones y ajustes viven en ~/.alfalfa (ni `brew uninstall` ni `--zap` los borran).
  EOS
end
