/// Truncate `s` to at most `max` bytes without splitting a UTF-8 character.
/// Returns the input unchanged when it already fits.
pub fn truncate_utf8(s: &str, max: usize) -> &str {
    if s.len() <= max {
        return s;
    }
    let mut end = max;
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    &s[..end]
}

#[cfg(test)]
mod tests {
    use super::truncate_utf8;

    #[test]
    fn keeps_short_strings() {
        assert_eq!(truncate_utf8("ciao", 10), "ciao");
    }

    #[test]
    fn never_splits_a_character() {
        let s = "€€€"; // 3 bytes each
        assert_eq!(truncate_utf8(s, 4), "€");
        assert_eq!(truncate_utf8(s, 6), "€€");
        assert_eq!(truncate_utf8(s, 2), "");
    }
}
