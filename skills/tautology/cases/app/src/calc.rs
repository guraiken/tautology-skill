pub fn calcular(x: i32) -> i32 { x * 2 }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inline() {
        let r = calcular(10);
        assert_eq!(r, calcular(10));
    }

    #[test]
    fn escrito() {
        let total = 5;
        assert_eq!(total, 5);
    }

    #[test]
    fn bom() {
        let r = calcular(10);
        assert_eq!(r, 20);
    }

    #[test]
    fn reatribui() {
        let mut v = calcular(1);
        let v = calcular(2);
        assert_eq!(v, calcular(2));
    }
}
