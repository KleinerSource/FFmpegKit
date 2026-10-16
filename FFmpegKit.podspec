native = File.directory?(File.join(__dir__, 'Frameworks')) ? '' : 'build/ios-native/pods/FFmpegKit/'

Pod::Spec.new do |s|
  s.name = 'FFmpegKit'
  s.version = '8.1.0'
  s.summary = 'Locally built FFmpeg 8.1 and Swift compatibility helpers'
  s.homepage = 'https://github.com/KleinerSource/FFmpegKit'
  s.authors = { 'FFmpeg contributors' => 'ffmpeg-devel@ffmpeg.org' }
  # CocoaPods validates the license path extension. The bundled license
  # collection remains a resource; use the repository's GPL notice here.
  s.license = { :type => 'GPL-3.0-or-later', :file => "#{native}LICENSE" }
  s.source = { :git => 'https://github.com/KleinerSource/FFmpegKit.git', :tag => 'n8.1' }
  s.ios.deployment_target = '16.0'
  s.static_framework = true
  s.source_files = 'Sources/FFmpegKit/**/*.{h,c}'
  s.public_header_files = 'Sources/FFmpegKit/include/*.h'
  s.resource_bundles = { 'FFmpegKit_FFmpegKit' => ["#{native}Licenses/**/*", "#{native}native-versions.json"] }
  s.vendored_frameworks = "#{native}Frameworks/Libav*.xcframework", "#{native}Frameworks/Libsw*.xcframework",
                          "#{native}Frameworks/libdav1d.xcframework", "#{native}Frameworks/libsrt.xcframework",
                          "#{native}Frameworks/gmp.xcframework", "#{native}Frameworks/nettle.xcframework",
                          "#{native}Frameworks/hogweed.xcframework", "#{native}Frameworks/gnutls.xcframework"
  s.frameworks = 'AudioToolbox', 'AVFoundation', 'CoreMedia', 'CoreVideo', 'VideoToolbox', 'Security'
  s.libraries = 'bz2', 'c++', 'iconv', 'resolv', 'z'
  s.dependency 'Libass'
end
