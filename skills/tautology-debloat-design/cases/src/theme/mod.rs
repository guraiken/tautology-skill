use gpui::rgb;

pub struct Palette {
    pub bg: gpui::Rgba,
    pub surface: gpui::Rgba,
    pub card: gpui::Rgba,
    pub border: gpui::Rgba,
    pub text: gpui::Rgba,
    pub strong: gpui::Rgba,
    pub muted: gpui::Rgba,
    pub primary: gpui::Rgba,
    pub card_radius: f32,
    pub control_radius: f32,
}

pub mod visual {
    pub const BODY: f32 = 13.;
    pub const FOOTNOTE: f32 = 12.;
    pub const HEADLINE: f32 = 18.;
}

pub fn default_palette() -> Palette {
    Palette {
        bg: rgb(0x111111),
        surface: rgb(0x181818),
        card: rgb(0x202020),
        border: rgb(0x333333),
        text: rgb(0xe5e5e5),
        strong: rgb(0xffffff),
        muted: rgb(0x999999),
        primary: rgb(0x2563eb),
        card_radius: 10.,
        control_radius: 6.,
    }
}
