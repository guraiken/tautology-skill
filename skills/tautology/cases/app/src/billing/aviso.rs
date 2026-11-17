use gpui::*;

// passa: garantia de pagamento longa fica para a leitura, não vira achado de tamanho.
fn pago(p: Palette) -> Div {
    div().child(div().child("A fatura foi emitida e o boleto do pagamento vale por 3 dias. Pague no app do banco antes que expire; depois disso a assinatura é suspensa e nada é cobrado."))
}
