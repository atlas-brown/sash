{
  description = "SaSh: Ahead-of-time analysis of shell program effects";

  inputs = {

    # Python 3.10 EOL in NixOS 26.05
    # Pin the release to nixos-25.11
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix_hammer_overrides = {
      url = "github:TyberiusPrime/uv2nix_hammer_overrides";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.treefmt-nix.inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = {
    nixpkgs,
    pyproject-nix,
    uv2nix,
    pyproject-build-systems,
    uv2nix_hammer_overrides,
    ...
  }: let
    inherit (nixpkgs) lib;
    forAllSystems = lib.genAttrs lib.systems.flakeExposed;
    pyproject = builtins.fromTOML (builtins.readFile ./pyproject.toml);
    inherit (pyproject.project) name description version;
    mainProgram = "sash";
    workspace = uv2nix.lib.workspace.loadWorkspace {workspaceRoot = ./.;};

    overlay = workspace.mkPyprojectOverlay {
      sourcePreference = "wheel";
    };

    editableOverlay = workspace.mkEditablePyprojectOverlay {
      root = "$REPO_ROOT";
    };

    meta = {
      inherit description version mainProgram;
      homepage = "https://github.com/atlas-brown/sash";
      license = lib.licenses.mit;
      platforms = lib.platforms.unix;
    };

    pythonSets = forAllSystems (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};

        python = lib.head (
          pyproject-nix.lib.util.filterPythonInterpreters {
            inherit (workspace) requires-python;
            inherit (pkgs) pythonInterpreters;
          }
        );

        pyprojectOverrides = final: prev: {
          ${name} = prev.${name}.overrideAttrs (old: {
            src = lib.cleanSourceWith {
              src = old.src;
              filter = path: type: let
                root = toString old.src;
              in
                !(builtins.elem path ["${root}/benchmarks" "${root}/results"])
                && lib.cleanSourceFilter path type;
            };
          });

          vcs-versioning = prev.vcs-versioning.overrideAttrs (old: {
            nativeBuildInputs =
              (old.nativeBuildInputs or [])
              ++ final.resolveBuildSystem {typing-extensions = [];};
          });

          libdash = prev.libdash.overrideAttrs (old: {
            nativeBuildInputs =
              (old.nativeBuildInputs or [])
              ++ (with pkgs; [autoconf automake libtool])
              ++ final.resolveBuildSystem {setuptools = [];};
            env = (old.env or {}) // {CFLAGS = "-std=gnu17";};
          });
        };
      in
        (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.default
            overlay
            (uv2nix_hammer_overrides.overrides pkgs)
            pyprojectOverrides
          ]
        )
    );

    sashPackages = forAllSystems (
      system: let
        pythonSet = pythonSets.${system};
        venv = pythonSet.mkVirtualEnv "${name}-${version}" workspace.deps.default;
      in
        venv.overrideAttrs (old: {
          meta = (old.meta or {}) // meta;
        })
    );
  in {
    packages = forAllSystems (system: {
      default = sashPackages.${system};
      asash = sashPackages.${system};
    });

    apps = forAllSystems (system: {
      default = {
        type = "app";
        program = lib.getExe sashPackages.${system};
        meta = {inherit description;};
      };
    });

    devShells = forAllSystems (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};
        editablePythonSet = pythonSets.${system}.overrideScope editableOverlay;
        virtualenv = editablePythonSet.mkVirtualEnv "sash-dev-env" workspace.deps.all;
      in {
        default = pkgs.mkShell {
          packages = [
            virtualenv
            pkgs.uv
            pkgs.git
          ];
          env = {
            UV_NO_SYNC = "1";
            UV_PYTHON = editablePythonSet.python.interpreter;
            UV_PYTHON_DOWNLOADS = "never";
          };
          shellHook = ''
            unset PYTHONPATH
            export REPO_ROOT=$(git rev-parse --show-toplevel)
          '';
        };
      }
    );

    checks = forAllSystems (system: {
      asash = sashPackages.${system};
    });

    formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.alejandra);
  };
}
