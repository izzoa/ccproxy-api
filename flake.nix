{
  description = "Development environment with Python, JavaScript, and Playwright";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # Define Playwright dependencies as a reusable list
        playwrightDeps = with pkgs; [
          glib
          nss
          nspr
          atk
          at-spi2-atk
          libdrm
          libxkbcommon
          gtk3
          pango
          cairo
          gdk-pixbuf
          xorg.libX11
          xorg.libxcb
          xorg.libXcomposite
          xorg.libXdamage
          xorg.libXext
          xorg.libXfixes
          xorg.libXrandr
          mesa
          libgbm
          expat
          alsa-lib
          at-spi2-core
          cups
          dbus
          fontconfig
          freetype
        ];

        # System libraries
        systemLibs = with pkgs; [
          stdenv.cc.cc.lib
          glibc
          zlib
          stdenv
        ];

        allLibs = systemLibs ++ playwrightDeps;
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs =
            with pkgs;
            [
              # Python with uv
              python3
              uv
              pyright

              # JavaScript tools
              nodejs
              bun
              pnpm

              # System tools
              bashInteractive
              duckdb
              chromium
              udev
            ]
            ++ allLibs;

          shellHook = ''
            # Set up LD_LIBRARY_PATH for Playwright and other native dependencies
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath allLibs}:$LD_LIBRARY_PATH"

            # Playwright configuration
            export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true

            # Optional: Set up uv if you want it to manage Python versions
            # export UV_PYTHON_PREFERENCE=system

            echo "Development environment loaded!"
            echo "Python: $(python3 --version)"
            echo "Node: $(node --version)"
            echo "Bun: $(bun --version)"
            echo "pnpm: $(pnpm --version)"
          '';

          # Environment variables
          PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
        };
      }
    );
}
