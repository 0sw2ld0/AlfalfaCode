# La app de Mac de AlfalfaCode. Este archivo vive en github.com/0sw2ld0/AlfalfaCode
# (distribucion/homebrew) y se copia al tap 0sw2ld0/homebrew-tap; la versión y la huella
# las cambia distribucion/actualizar-tap.sh a partir de cada release.
cask "alfalfacode" do
  version "0.99.2"
  sha256 "23cacadb3a4c44c4fa93b84e419ce50523524f2b21fca634d062522fa1e51952"

  url "https://github.com/0sw2ld0/AlfalfaCode/releases/download/v#{version}/AlfalfaCode-#{version}.dmg",
      verified: "github.com/0sw2ld0/AlfalfaCode/"
  name "AlfalfaCode"
  desc "Arnés de agentes de programación: Ollama, OpenAI, Anthropic y modelos gratis"
  homepage "https://alfalfacode.cuycoders.dev"

  livecheck do
    url :url
    strategy :github_latest
  end

  # La app se actualiza sola (lee version.json de la última release, comprueba huella,
  # firma y notarización). Con esto `brew upgrade` no la toca. Ojo con --greedy: Homebrew
  # compara con la versión con que la instaló él, no con la que trajo el actualizador, así
  # que si este cask va por detrás de la app puede bajarla de versión.
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
