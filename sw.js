// 1. الاستماع لحدث الـ Push (لما السيرفر يبعت بيانات الإشعار)
self.addEventListener('push', function(event) {
    if (event.data) {
        try {
            // تحويل البيانات القادمة من السيرفر لـ JSON
            const data = event.data.json();
            
            // إعدادات شكل الإشعار
            const options = {
                body: data.body, // نص الرسالة
                icon: 'https://cdn-icons-png.flaticon.com/512/3594/3594349.png', // أيقونة الإشعار (تقدر تغيرها للوجو بتاعك)
                badge: 'https://cdn-icons-png.flaticon.com/512/3594/3594349.png', // أيقونة صغيرة بتظهر في الموبايل فوق
                vibrate: [100, 50, 100], // نمط الاهتزاز للموبايلات
                data: {
                    dateOfArrival: Date.now(),
                    primaryKey: '1'
                }
            };

            // أمر إظهار الإشعار للمستخدم
            event.waitUntil(
                self.registration.showNotification(data.title, options)
            );
        } catch (e) {
            console.error("خطأ في قراءة بيانات الإشعار:", e);
        }
    }
});

// 2. الاستماع لحدث الضغط على الإشعار
self.addEventListener('notificationclick', function(event) {
    // قفل الإشعار بعد ما المستخدم يدوس عليه
    event.notification.close();

    // فتح الموقع تلقائياً لما المستخدم يضغط على الإشعار
    event.waitUntil(
        clients.openWindow('/') // هيفتح الصفحة الرئيسية للموقع
    );
});