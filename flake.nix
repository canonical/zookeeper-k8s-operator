{
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = {
    self,
    nixpkgs,
  }: let
    pkgs = import nixpkgs {system = "x86_64-linux";};
  in {
    devShells.x86_64-linux.default = pkgs.mkShell {
      packages = with pkgs; [
        nodejs_20
      ];

      shellHook = ''
        export RENOVATE_CONFIG_FILE=./renovate.json5
        export LOG_LEVEL=debug
      '';
    };
  };
}
