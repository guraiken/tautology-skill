source: test cases
exclude: worker/
components: src/components/ src/screens/
layer 1: src/data/
layer 3: src/components/
layer 4: src/screens/
target 1: src/data/apoio.rs
target 3: src/components/visual.rs
verify: cargo test
