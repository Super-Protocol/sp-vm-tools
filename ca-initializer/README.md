# Super Protocol CA Initializer CLI
A tool for initialize VM and create necessary certificates

## Setup
1. Install dependencies:

    ```
    npm i
    ```
2. Run help in dev mode to see available commands:

    ```
    npm run dev help
    ```

## Building
### Linux
#### Build in docker (recommended):
```
cd linux_builder && ./build.sh && cd ..
```
Can be run on any os with Docker support.</br>
Result will be saved at `./dist/ca-initialize-linux`
#### Build on native os:
```
npm run build
```
Result will be saved at `./dist/ca-initialize`

### MacOS
```
npm run build
```
Result will be saved at `./dist/ca-initialize`

## Commands
`npm run build:prepare` – compiles typescript with tcs.</br>
`npm run build` - builds a binary file (target will be autodetected)</br>
`npm run build:win:x64` - builds a binary file for Windows with arch x64</br>
`npm run build:win:arm64` - builds a binary file for Windows with arch arm64</br>
`npm run build:linux:x64` – builds a binary file for Linux with arch x64</br>
`npm run build:linux:arm64` – builds a binary file for Linux with arch arm64</br>
`npm run build:macos:x64` – builds a macos binary file for MacOS with arch x64</br>
`npm run build:macos:arm64` – builds a macos binary file for MacOS with arch arm64</br>
`npm run dev -- [command]` – runs command in dev mode</br>
`npm run prettier` – runs code auto formatting

## Dependencies
- NodeJS v17.4.0
- NPM v8.3.1
