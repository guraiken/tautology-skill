#[cfg(test)]
mod tests {
    #[test]
    fn antes_depois() {
        let antes = ler();
        mudar();
        assert_eq!(ler(), antes);
    }
}
