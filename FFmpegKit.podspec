native = File.directory?(File.join(__dir__, 'Frameworks')) ? '' : 'build/ios-native/pods/FFmpegKit/'
frameworks = File.expand_path("#{native}Frameworks", __dir__)
force_load = lambda do |slice|
  # dav1d 7 has smaller public structs than media_kit's dav1d 6. Keep its
  # implementation with the FFmpeg headers it was compiled against.
  %w[Libavcodec Libavdevice Libavfilter Libavformat Libavutil Libswresample Libswscale libdav1d].map { |name|
    "\"-Wl,-force_load,#{frameworks}/#{name}.xcframework/#{slice}/#{name}.framework/#{name}\""
  }.join(' ')
end

Pod::Spec.new do |s|
  s.name = 'FFmpegKit'
  s.version = '8.1.0'
  s.summary = 'Locally built FFmpeg 8.1 and Swift compatibility helpers'
  s.homepage = 'https://github.com/KleinerSource/FFmpegKit'
  s.authors = { 'FFmpeg contributors' => 'ffmpeg-devel@ffmpeg.org' }
  # CocoaPods validates the license path extension. The bundled license
  # collection remains a resource; use the repository's GPL notice here.
  s.license = { :type => 'GPL-3.0-or-later', :file => 'LICENSE' }
  s.source = { :git => 'https://github.com/KleinerSource/FFmpegKit.git', :tag => 'n8.1' }
  s.ios.deployment_target = '16.0'
  s.static_framework = true
  # Use existing slices: Xcode validates force-load inputs before CocoaPods
  # copies XCFrameworks into the build intermediates directory.
  s.user_target_xcconfig = {
    'OTHER_LDFLAGS' => '$(inherited) $(FFMPEGKIT_FORCE_LOAD_FLAGS)',
    'FFMPEGKIT_FORCE_LOAD_FLAGS[sdk=iphoneos*]' => force_load.call('ios-arm64'),
    'FFMPEGKIT_FORCE_LOAD_FLAGS[sdk=iphonesimulator*]' => force_load.call('ios-arm64_x86_64-simulator')
  }
  s.source_files = 'Sources/FFmpegKit/**/*.{h,c}'
  s.public_header_files = 'Sources/FFmpegKit/include/*.h'
  s.resource_bundles = { 'FFmpegKit_FFmpegKit' => ["#{native}Licenses", "#{native}native-versions.json"] }
  s.vendored_frameworks = "#{native}Frameworks/Libav*.xcframework", "#{native}Frameworks/Libsw*.xcframework",
                          "#{native}Frameworks/libdav1d.xcframework", "#{native}Frameworks/libsrt.xcframework",
                          "#{native}Frameworks/gmp.xcframework", "#{native}Frameworks/nettle.xcframework",
                          "#{native}Frameworks/hogweed.xcframework", "#{native}Frameworks/gnutls.xcframework"
  s.frameworks = 'AudioToolbox', 'AVFoundation', 'CoreMedia', 'CoreVideo', 'VideoToolbox', 'Security'
  s.libraries = 'bz2', 'c++', 'iconv', 'resolv', 'z'
  s.dependency 'Libass'
end
