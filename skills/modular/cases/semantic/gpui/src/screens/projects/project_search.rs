use gpui::*;

use crate::theme::Theme;

pub struct ProjectSearch {
    query: SharedString,
    focus: FocusHandle,
}

impl Render for ProjectSearch {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let theme = cx.global::<Theme>();
        h_flex()
            .id("project-search")
            .track_focus(&self.focus)
            .w_full()
            .h(px(32.))
            .px_2()
            .gap_2()
            .rounded(px(6.))
            .bg(theme.input_bg)
            .border_1()
            .border_color(theme.border)
            .child(Icon::new(IconName::Search).size_4().text_color(theme.muted))
            .child(
                div()
                    .flex_1()
                    .text_sm()
                    .text_color(if self.query.is_empty() { theme.muted } else { theme.text })
                    .child(if self.query.is_empty() { "Search projects".into() } else { self.query.clone() }),
            )
            .when(!self.query.is_empty(), |el| {
                el.child(
                    div()
                        .id("clear")
                        .cursor_pointer()
                        .child(Icon::new(IconName::Close).size_4())
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.query = SharedString::default();
                            cx.notify();
                        })),
                )
            })
    }
}
