use gpui::*;

use crate::theme::Theme;

pub struct FileFilter {
    text: SharedString,
    focus_handle: FocusHandle,
}

impl Render for FileFilter {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let t = cx.global::<Theme>();
        h_flex()
            .id("file-filter")
            .track_focus(&self.focus_handle)
            .w_full()
            .h(px(32.))
            .px_2()
            .gap_2()
            .rounded(px(6.))
            .bg(t.input_bg)
            .border_1()
            .border_color(t.border)
            .child(
                div()
                    .flex_1()
                    .text_sm()
                    .text_color(if self.text.is_empty() { t.muted } else { t.text })
                    .child(if self.text.is_empty() { "Filter files".into() } else { self.text.clone() }),
            )
            .child(Icon::new(IconName::Search).size_4().text_color(t.muted))
            .on_key_down(cx.listener(|this, ev: &KeyDownEvent, _, cx| {
                if ev.keystroke.key == "enter" {
                    cx.emit(FilterSubmitted(this.text.clone()));
                }
            }))
    }
}
